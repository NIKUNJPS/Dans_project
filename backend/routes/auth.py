"""Authentication routes: signup, verify-otp, login, forgot/reset password, refresh, me."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from db import get_db
from email_service import (
    render_otp_email,
    render_password_changed_email,
    send_email,
)
from models import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserPublic,
    VerifyOtpRequest,
    VerifyResetOtpRequest,
)
from security import (
    create_access_token,
    create_refresh_token,
    create_reset_token,
    decode_token,
    generate_otp,
    get_current_user,
    hash_otp,
    hash_password,
    sha256_hex,
    validate_password_strength,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _send_email_async(to: str, subject: str, html: str, text: str) -> bool:
    """Run the blocking email send off the event loop. Returns delivery success."""
    return await asyncio.to_thread(send_email, to, subject, html, text)


def _public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "email": u["email"],
        "first_name": u.get("first_name", ""),
        "last_name": u.get("last_name", ""),
        "role": u.get("role", "detailer"),
        "company": u.get("company", ""),
        "country": u.get("country", ""),
        "phone": u.get("phone", ""),
        "avatar_url": u.get("avatar_url", ""),
        "is_verified": u.get("is_verified", False),
        "is_active": u.get("is_active", True),
        "subscription_tier": u.get("subscription_tier", "free"),
        "created_by": u.get("created_by"),
        "usage_this_month": u.get("usage_this_month", {"analyses": 0, "files_processed": 0, "total_file_size_mb": 0}),
        "created_at": u.get("created_at", datetime.now(timezone.utc)),
    }


async def _audit(db, user_id: str, action: str, resource: str, resource_id: str = "", req: Request | None = None) -> None:
    doc = {
        "id": sha256_hex(f"{user_id}{action}{resource}{datetime.now(timezone.utc).isoformat()}"),
        "user_id": user_id,
        "action": action,
        "resource": resource,
        "resource_id": resource_id,
        "ip_address": req.client.host if req and req.client else "",
        "user_agent": req.headers.get("user-agent", "") if req else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await db.audit_log.insert_one(doc)


@router.post("/signup")
async def signup(data: SignupRequest):
    db = get_db()
    err = validate_password_strength(data.password)
    if err:
        raise HTTPException(status_code=422, detail=err)

    existing = await db.users.find_one({"email": data.email.lower()})
    if existing:
        raise HTTPException(status_code=409, detail="Email is already registered")

    otp = generate_otp()
    now = datetime.now(timezone.utc)
    user_id = sha256_hex(f"{data.email}{now.isoformat()}")[:32]

    user = {
        "id": user_id,
        "email": data.email.lower(),
        "password_hash": hash_password(data.password),
        "first_name": data.first_name,
        "last_name": data.last_name,
        "company": data.company,
        "country": data.country,
        "role": data.role,
        "phone": "",
        "avatar_url": "",
        "is_verified": False,
        "is_active": True,
        "subscription_tier": "free",
        "created_by": None,
        "usage_this_month": {"analyses": 0, "files_processed": 0, "total_file_size_mb": 0},
        "otp_hash": hash_otp(otp),
        "otp_purpose": "signup",
        "otp_expiry": (now + timedelta(seconds=300)).isoformat(),
        "otp_attempts": 0,
        "blockchain_hash": sha256_hex(f"signup:{data.email}:{now.isoformat()}"),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    await db.users.insert_one(user)

    subj, html, text = render_otp_email(data.first_name, otp, "signup")
    sent = await _send_email_async(data.email, subj, html, text)
    if not sent:
        raise HTTPException(
            status_code=502,
            detail="We couldn't send your verification code right now. Please try again in a moment.",
        )

    return {
        "message": "We sent a 6-digit verification code to your email.",
        "user_id": user_id,
        "email": data.email.lower(),
    }


@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(data: VerifyOtpRequest, request: Request):
    db = get_db()
    user = await db.users.find_one({"id": data.user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("otp_attempts", 0) >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new code.")

    expiry = user.get("otp_expiry")
    if not expiry or datetime.fromisoformat(expiry) < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Code expired. Please request a new one.")

    if hash_otp(data.otp.strip()) != user.get("otp_hash"):
        await db.users.update_one({"id": user["id"]}, {"$inc": {"otp_attempts": 1}})
        raise HTTPException(status_code=401, detail="Invalid verification code")

    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "is_verified": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"otp_hash": "", "otp_purpose": "", "otp_expiry": "", "otp_attempts": ""},
        },
    )
    await _audit(db, user["id"], "otp_verified", "user", user["id"], request)

    access = create_access_token(user["id"], user["role"])
    refresh = create_refresh_token(user["id"])

    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 0})
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": _public_user(fresh),
    }


@router.post("/login")
async def login(data: LoginRequest, request: Request):
    """Login step-1: verify credentials, send OTP for 2FA (first-time login + any login)."""
    db = get_db()
    user = await db.users.find_one({"email": data.email.lower()})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account is disabled")

    # If first-time login (not verified yet), require OTP as "signup" verification
    if not user.get("is_verified", False):
        otp = generate_otp()
        now = datetime.now(timezone.utc)
        await db.users.update_one(
            {"id": user["id"]},
            {
                "$set": {
                    "otp_hash": hash_otp(otp),
                    "otp_purpose": "signup",
                    "otp_expiry": (now + timedelta(seconds=300)).isoformat(),
                    "otp_attempts": 0,
                }
            },
        )
        subj, html, text = render_otp_email(user["first_name"], otp, "signup")
        sent = await _send_email_async(user["email"], subj, html, text)
        if not sent:
            raise HTTPException(
                status_code=502,
                detail="We couldn't send your verification code right now. Please try again in a moment.",
            )
        return {
            "mfa_required": True,
            "message": "Please verify your email. We sent a new code.",
            "user_id": user["id"],
            "email": user["email"],
        }

    # Standard login — issue tokens (no 2FA OTP per MVP scope)
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "last_login": datetime.now(timezone.utc).isoformat(),
                "last_login_ip": request.client.host if request.client else "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    await _audit(db, user["id"], "login", "user", user["id"], request)

    access = create_access_token(user["id"], user["role"])
    refresh = create_refresh_token(user["id"])
    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 0})
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user": _public_user(fresh),
    }


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest):
    db = get_db()
    user = await db.users.find_one({"email": data.email.lower()})
    if not user:
        # Return generic success to prevent email enumeration
        return {"message": "If that email exists, a reset code has been sent.", "reset_token": ""}

    otp = generate_otp()
    now = datetime.now(timezone.utc)
    reset_token = create_reset_token(user["id"], "reset_pending")
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "otp_hash": hash_otp(otp),
                "otp_purpose": "reset",
                "otp_expiry": (now + timedelta(seconds=300)).isoformat(),
                "otp_attempts": 0,
            }
        },
    )

    subj, html, text = render_otp_email(user["first_name"], otp, "reset")
    # Generic response regardless of delivery to avoid email enumeration; failures are logged.
    await _send_email_async(user["email"], subj, html, text)
    return {
        "message": "If that email exists, a reset code has been sent.",
        "reset_token": reset_token,
        "email": user["email"],
    }


@router.post("/verify-reset-otp")
async def verify_reset_otp(data: VerifyResetOtpRequest):
    payload = decode_token(data.reset_token)
    if payload.get("type") != "reset_pending":
        raise HTTPException(status_code=400, detail="Invalid reset token")
    db = get_db()
    user = await db.users.find_one({"id": payload["sub"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("otp_attempts", 0) >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new code.")
    expiry = user.get("otp_expiry")
    if not expiry or datetime.fromisoformat(expiry) < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Code expired. Please request a new one.")
    if hash_otp(data.otp.strip()) != user.get("otp_hash"):
        await db.users.update_one({"id": user["id"]}, {"$inc": {"otp_attempts": 1}})
        raise HTTPException(status_code=401, detail="Invalid verification code")

    session_token = create_reset_token(user["id"], "reset_session")
    return {"reset_session_token": session_token}


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest, request: Request):
    payload = decode_token(data.reset_session_token)
    if payload.get("type") != "reset_session":
        raise HTTPException(status_code=400, detail="Invalid session token")
    db = get_db()
    user = await db.users.find_one({"id": payload["sub"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    err = validate_password_strength(data.new_password)
    if err:
        raise HTTPException(status_code=422, detail=err)

    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "password_hash": hash_password(data.new_password),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"otp_hash": "", "otp_purpose": "", "otp_expiry": "", "otp_attempts": ""},
        },
    )
    await _audit(db, user["id"], "password_reset", "user", user["id"], request)

    subj, html, text = render_password_changed_email(user["first_name"])
    await _send_email_async(user["email"], subj, html, text)
    return {"message": "Password reset successful. You can now sign in with your new password."}


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest, request: Request, user=Depends(get_current_user)
):
    db = get_db()
    full = await db.users.find_one({"id": user["id"]})
    if not verify_password(data.current_password, full.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    err = validate_password_strength(data.new_password)
    if err:
        raise HTTPException(status_code=422, detail=err)

    # Request OTP if not provided
    if not data.otp:
        otp = generate_otp()
        now = datetime.now(timezone.utc)
        await db.users.update_one(
            {"id": user["id"]},
            {
                "$set": {
                    "otp_hash": hash_otp(otp),
                    "otp_purpose": "change",
                    "otp_expiry": (now + timedelta(seconds=300)).isoformat(),
                    "otp_attempts": 0,
                }
            },
        )
        subj, html, text = render_otp_email(user["first_name"], otp, "change")
        sent = await _send_email_async(user["email"], subj, html, text)
        if not sent:
            raise HTTPException(
                status_code=502,
                detail="We couldn't send your verification code right now. Please try again in a moment.",
            )
        return {"otp_required": True, "message": "We sent a verification code to confirm this change."}

    if hash_otp(data.otp.strip()) != full.get("otp_hash") or full.get("otp_purpose") != "change":
        raise HTTPException(status_code=401, detail="Invalid verification code")
    expiry = full.get("otp_expiry")
    if not expiry or datetime.fromisoformat(expiry) < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Code expired")

    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "password_hash": hash_password(data.new_password),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"otp_hash": "", "otp_purpose": "", "otp_expiry": "", "otp_attempts": ""},
        },
    )
    await _audit(db, user["id"], "password_change", "user", user["id"], request)
    subj, html, text = render_password_changed_email(user["first_name"])
    await _send_email_async(user["email"], subj, html, text)
    return {"message": "Password changed successfully."}


@router.post("/refresh")
async def refresh(data: RefreshRequest):
    payload = decode_token(data.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=400, detail="Invalid refresh token")
    db = get_db()
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    access = create_access_token(user["id"], user["role"])
    refresh = create_refresh_token(user["id"])
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


@router.post("/resend-otp")
async def resend_otp(payload: dict):
    """Generic OTP resend using user_id (used by signup/verify-otp page)."""
    db = get_db()
    uid = payload.get("user_id")
    user = await db.users.find_one({"id": uid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    otp = generate_otp()
    now = datetime.now(timezone.utc)
    purpose = user.get("otp_purpose") or "signup"
    await db.users.update_one(
        {"id": uid},
        {
            "$set": {
                "otp_hash": hash_otp(otp),
                "otp_purpose": purpose,
                "otp_expiry": (now + timedelta(seconds=300)).isoformat(),
                "otp_attempts": 0,
            }
        },
    )
    subj, html, text = render_otp_email(user["first_name"], otp, purpose)
    sent = await _send_email_async(user["email"], subj, html, text)
    if not sent:
        raise HTTPException(
            status_code=502,
            detail="We couldn't send your verification code right now. Please try again in a moment.",
        )
    return {"message": "New code sent."}


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return _public_user(user)


@router.put("/me")
async def update_me(payload: dict, user=Depends(get_current_user)):
    db = get_db()
    allowed = {"first_name", "last_name", "company", "country", "phone", "avatar_url"}
    updates = {k: v for k, v in payload.items() if k in allowed and v is not None}
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.users.update_one({"id": user["id"]}, {"$set": updates})
    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 0})
    return _public_user(fresh)


@router.post("/logout")
async def logout(request: Request, user=Depends(get_current_user)):
    db = get_db()
    await _audit(db, user["id"], "logout", "user", user["id"], request)
    return {"message": "Logged out"}
