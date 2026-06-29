"""Security utilities: bcrypt password hashing, JWT tokens, OTP generation."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings
from db import get_db

bearer_scheme = HTTPBearer(auto_error=False)


# ---------- PASSWORD ----------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def validate_password_strength(password: str) -> Optional[str]:
    """Return error string if invalid, None if ok."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not any(c.isupper() for c in password):
        return "Password must include at least one uppercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must include at least one number"
    if not any(not c.isalnum() for c in password):
        return "Password must include at least one special character"
    return None


# ---------- OTP ----------
def generate_otp(length: int | None = None) -> str:
    n = length or settings.otp_length
    rng = secrets.SystemRandom()
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


# ---------- JWT ----------
def _encode(payload: dict, expires_delta: timedelta) -> str:
    to_encode = payload.copy()
    to_encode.update(
        {
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + expires_delta,
        }
    )
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, role: str) -> str:
    return _encode(
        {"sub": user_id, "role": role, "type": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: str) -> str:
    return _encode(
        {"sub": user_id, "type": "refresh", "jti": secrets.token_urlsafe(16)},
        timedelta(days=settings.refresh_token_expire_days),
    )


def create_reset_token(user_id: str, purpose: str) -> str:
    return _encode(
        {"sub": user_id, "type": purpose, "jti": secrets.token_urlsafe(16)},
        timedelta(minutes=15),
    )


def create_impersonation_token(target_user_id: str, target_role: str, admin_id: str) -> str:
    """Short-lived read-only JWT for super_admin impersonation. 15 min expiry."""
    return _encode(
        {
            "sub": target_user_id,
            "role": target_role,
            "type": "access",
            "impersonated_by": admin_id,
            "read_only": True,
            "jti": secrets.token_urlsafe(12),
        },
        timedelta(minutes=15),
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ---------- HASH CONTENT (for blockchain-style verification) ----------
def sha256_hex(content: bytes | str) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


# ---------- DEPENDENCIES ----------
async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    db = get_db()
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account disabled")
    # Surface impersonation metadata for downstream guards/audit
    user["_impersonated_by"] = payload.get("impersonated_by")
    user["_read_only"] = bool(payload.get("read_only"))
    request.state.user = user
    return user


def require_roles(*roles: str):
    async def _dep(user=Depends(get_current_user)):
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _dep


async def get_super_admin(user=Depends(get_current_user)):
    if user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    if user.get("_read_only"):
        # Block writes for impersonation sessions — applies at the endpoint level
        # when this dep is wired to a mutating route. GET routes use get_current_user.
        raise HTTPException(status_code=403, detail="Impersonation session — read only.")
    return user


def block_write_if_readonly(user: dict) -> None:
    """Raise 403 if the current session is a read-only impersonation token."""
    if user.get("_read_only"):
        raise HTTPException(status_code=403, detail="Impersonation session — read only.")


async def get_current_admin(user=Depends(get_current_user)):
    """Backwards-compat alias used by older routes."""
    if user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
