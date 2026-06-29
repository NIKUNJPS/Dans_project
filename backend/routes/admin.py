"""Super Admin routes: user CRUD, password reset, feature permissions, analytics,
audit-log query, mode catalog, impersonation token.

Every mutating route is guarded by `get_super_admin` which already blocks impersonated
read-only sessions, so super_admin → super_admin write loops are impossible.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from db import get_db
from middleware.permission_guard import audit_log
from models import (
    AdminPasswordReset,
    AdminUserCreate,
    AdminUserUpdate,
    FeaturePermissionsUpdate,
    ImpersonateRequest,
)
from permissions import (
    default_permissions,
    get_permissions,
    super_admin_permissions,
    update_permissions,
)
from prompts.prompt_router import list_all_modes_flat
from security import (
    create_impersonation_token,
    get_current_user,
    get_super_admin,
    hash_password,
    sha256_hex,
    validate_password_strength,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── ANALYTICS / DASHBOARD ──────────────────────────────────────────────
@router.get("/analytics")
async def admin_analytics(user=Depends(get_super_admin)):
    db = get_db()
    total_users = await db.users.count_documents({})
    active_users = await db.users.count_documents({"is_active": True})
    total_projects = await db.projects.count_documents({})
    total_analyses = await db.analyses.count_documents({})
    complete = await db.analyses.count_documents({"status": "complete"})
    failed = await db.analyses.count_documents({"status": "failed"})
    total_files = await db.files.count_documents({})
    total_rfis = await db.rfis.count_documents({})
    total_estimates = await db.estimates.count_documents({})

    by_role = []
    async for d in db.users.aggregate([{"$group": {"_id": "$role", "c": {"$sum": 1}}}]):
        by_role.append({"role": d["_id"], "count": d["c"]})

    # Top 7 modes used this month (YYYY-MM)
    ym_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    top_modes = []
    async for d in db.analyses.aggregate([
        {"$match": {"created_at": {"$regex": f"^{ym_prefix}"}}},
        {"$group": {"_id": "$mode", "c": {"$sum": 1}}},
        {"$sort": {"c": -1}},
        {"$limit": 7},
    ]):
        top_modes.append({"mode": d["_id"], "count": d["c"]})

    return {
        "total_users": total_users,
        "active_users": active_users,
        "total_projects": total_projects,
        "total_analyses": total_analyses,
        "complete_analyses": complete,
        "failed_analyses": failed,
        "total_files": total_files,
        "total_rfis": total_rfis,
        "total_estimates": total_estimates,
        "users_by_role": by_role,
        "top_modes_this_month": top_modes,
    }


# ─── USERS ──────────────────────────────────────────────────────────────
@router.get("/users")
async def list_users(user=Depends(get_super_admin)):
    db = get_db()
    items = await db.users.find(
        {}, {"_id": 0, "password_hash": 0, "otp_hash": 0, "otp_expiry": 0, "otp_purpose": 0}
    ).sort("created_at", -1).to_list(500)

    # Attach usage rollup for the current month
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    usage_map: dict[str, int] = {}
    async for u in db.usage_records.find({"yearMonth": ym}, {"_id": 0, "userId": 1, "analysesCount": 1}):
        usage_map[u["userId"]] = u.get("analysesCount", 0)
    for i in items:
        i["analyses_this_month"] = usage_map.get(i["id"], 0)
    return {"items": items, "total": len(items)}


@router.post("/users")
async def create_user(payload: AdminUserCreate, request: Request, user=Depends(get_super_admin)):
    db = get_db()
    err = validate_password_strength(payload.password)
    if err:
        raise HTTPException(status_code=422, detail=err)
    if await db.users.find_one({"email": payload.email.lower()}):
        raise HTTPException(status_code=409, detail="Email already registered")
    now = _now()
    uid = sha256_hex(f"{payload.email}{now}")[:32]
    doc = {
        "id": uid,
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "company": payload.company,
        "country": payload.country,
        "role": payload.role,
        "phone": "",
        "avatar_url": "",
        "is_verified": True,        # admin-created accounts skip email verification
        "is_active": True,
        "subscription_tier": "free",
        "created_by": user["id"],
        "usage_this_month": {"analyses": 0, "files_processed": 0, "total_file_size_mb": 0},
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(doc)
    # Seed default feature_permissions
    await db.feature_permissions.insert_one({
        "userId": uid,
        "role": payload.role,
        **default_permissions(payload.role),
        "lastUpdatedBy": user["id"],
        "createdAt": now,
        "updatedAt": now,
    })
    await audit_log(user["id"], "admin.user.create", "user", uid, request,
                    extra={"email": payload.email, "role": payload.role})
    doc.pop("_id", None)
    doc.pop("password_hash", None)
    return doc


@router.get("/users/{uid}")
async def get_user(uid: str, user=Depends(get_super_admin)):
    db = get_db()
    u = await db.users.find_one(
        {"id": uid},
        {"_id": 0, "password_hash": 0, "otp_hash": 0, "otp_expiry": 0, "otp_purpose": 0},
    )
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    perms = await get_permissions(uid, u["role"])
    perms.pop("_id", None)
    return {"user": u, "permissions": perms}


@router.put("/users/{uid}")
async def update_user(uid: str, payload: AdminUserUpdate, request: Request, user=Depends(get_super_admin)):
    db = get_db()
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "super_admin" and uid != user["id"]:
        # prevent demoting / deactivating other super admins
        forbidden = {"role", "is_active"}
        if any(k in forbidden and getattr(payload, k) is not None for k in forbidden):
            raise HTTPException(status_code=403, detail="Cannot modify another super admin's role/status.")
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if updates:
        updates["updated_at"] = _now()
        await db.users.update_one({"id": uid}, {"$set": updates})
    fresh = await db.users.find_one(
        {"id": uid}, {"_id": 0, "password_hash": 0, "otp_hash": 0}
    )
    await audit_log(user["id"], "admin.user.update", "user", uid, request, extra={"changes": list(updates.keys())})
    return fresh


@router.delete("/users/{uid}")
async def disable_user(uid: str, request: Request, user=Depends(get_super_admin)):
    if uid == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot disable yourself.")
    db = get_db()
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "super_admin":
        raise HTTPException(status_code=403, detail="Cannot disable a super admin.")
    await db.users.update_one(
        {"id": uid}, {"$set": {"is_active": False, "updated_at": _now()}},
    )
    await audit_log(user["id"], "admin.user.disable", "user", uid, request)
    return {"message": "User disabled"}


@router.post("/users/{uid}/password-reset")
async def reset_user_password(uid: str, payload: AdminPasswordReset, request: Request, user=Depends(get_super_admin)):
    db = get_db()
    err = validate_password_strength(payload.new_password)
    if err:
        raise HTTPException(status_code=422, detail=err)
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    await db.users.update_one(
        {"id": uid},
        {"$set": {"password_hash": hash_password(payload.new_password), "updated_at": _now()}},
    )
    await audit_log(user["id"], "admin.user.password_reset", "user", uid, request)
    return {"message": "Password reset successful."}


# ─── FEATURE PERMISSIONS ────────────────────────────────────────────────
@router.get("/users/{uid}/permissions")
async def get_user_permissions(uid: str, user=Depends(get_super_admin)):
    db = get_db()
    target = await db.users.find_one({"id": uid}, {"_id": 0, "role": 1})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    perms = await get_permissions(uid, target["role"])
    perms.pop("_id", None)
    return perms


@router.put("/users/{uid}/permissions")
async def update_user_permissions(
    uid: str,
    payload: FeaturePermissionsUpdate,
    request: Request,
    user=Depends(get_super_admin),
):
    db = get_db()
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "super_admin":
        raise HTTPException(status_code=400, detail="Super admins have wildcard permissions; cannot edit.")
    updates = payload.model_dump(exclude_unset=True)
    fresh = await update_permissions(uid, updates, user["id"])
    fresh.pop("_id", None)
    await audit_log(user["id"], "admin.permissions.update", "user", uid, request, extra={"changes": list(updates.keys())})
    return fresh


# ─── MODES CATALOG ──────────────────────────────────────────────────────
@router.get("/modes/all")
async def all_modes(user=Depends(get_super_admin)):
    """Every mode across every role — used by the Permission Editor."""
    return {"modes": list_all_modes_flat()}


# ─── AUDIT LOG ──────────────────────────────────────────────────────────
@router.get("/audit-log")
async def audit_logs(
    limit: int = 200,
    user_id: str | None = None,
    action: str | None = None,
    user=Depends(get_super_admin),
):
    db = get_db()
    q: dict = {}
    if user_id:
        q["user_id"] = user_id
    if action:
        q["action"] = {"$regex": f"^{action}"}
    items = await db.audit_log.find(q, {"_id": 0}).sort("timestamp", -1).to_list(min(limit, 1000))
    return {"items": items, "total": len(items)}


# ─── IMPERSONATION ──────────────────────────────────────────────────────
@router.post("/impersonate")
async def impersonate(payload: ImpersonateRequest, request: Request, user=Depends(get_super_admin)):
    db = get_db()
    target = await db.users.find_one({"id": payload.user_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == "super_admin":
        raise HTTPException(status_code=400, detail="Cannot impersonate another super admin.")
    token = create_impersonation_token(target["id"], target["role"], user["id"])
    await audit_log(user["id"], "admin.impersonate", "user", target["id"], request)
    return {
        "access_token": token,
        "token_type": "bearer",
        "read_only": True,
        "target_user": {
            "id": target["id"],
            "email": target["email"],
            "first_name": target.get("first_name", ""),
            "last_name": target.get("last_name", ""),
            "role": target["role"],
        },
        "expires_in_minutes": 15,
    }


# ─── SELF (for the loaded admin's permissions surface) ──────────────────
@router.get("/me/permissions")
async def my_permissions(user=Depends(get_current_user)):
    """Convenience endpoint for any logged-in user to fetch their own permissions."""
    if user["role"] == "super_admin":
        return super_admin_permissions(user["id"])
    perms = await get_permissions(user["id"], user["role"])
    perms.pop("_id", None)
    return perms
