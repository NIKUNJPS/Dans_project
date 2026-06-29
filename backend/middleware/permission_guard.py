"""Permission middleware — gates analysis, exports, estimation against feature_permissions."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

from db import get_db
from permissions import (
    export_allowed,
    get_permissions,
    mode_allowed,
)
from security import get_current_user


async def load_permissions(request: Request, user=Depends(get_current_user)) -> dict:
    """Attach feature_permissions to request.state and return them."""
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Account suspended. Contact admin.")
    perms = await get_permissions(user["id"], user["role"])
    request.state.permissions = perms
    request.state.current_user = user
    return perms


def require_mode(mode_id_field: str = "mode"):
    async def _dep(request: Request, perms: dict = Depends(load_permissions)):
        # mode_id comes from the route handler body — defer the actual check
        # to the handler since it parses the body. This dep just ensures perms are loaded.
        return perms
    return _dep


def check_mode_access(perms: dict, mode_id: str):
    if perms.get("role") == "super_admin":
        return
    if not mode_allowed(perms, mode_id):
        raise HTTPException(
            status_code=403,
            detail=f'Mode "{mode_id}" is not enabled for your account. Contact your administrator.',
        )


def check_export_access(perms: dict, fmt: str):
    if perms.get("role") == "super_admin":
        return
    if not export_allowed(perms, fmt):
        raise HTTPException(
            status_code=403,
            detail=f'Export format "{fmt}" is not enabled for your account. Contact your administrator.',
        )


def check_feature(perms: dict, flag: str):
    if perms.get("role") == "super_admin":
        return
    if not perms.get(flag, False):
        raise HTTPException(
            status_code=403,
            detail=f'Feature "{flag}" is not enabled for your account. Contact your administrator.',
        )


def check_country(perms: dict, country: str):
    if perms.get("role") == "super_admin":
        return
    allowed = perms.get("estimationCountries", [])
    if country not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f'Country "{country}" is not enabled for your account. Contact your administrator.',
        )


def check_file_size(perms: dict, size_mb: float):
    if perms.get("role") == "super_admin":
        return
    cap = perms.get("maxFileSizeMb", 25)
    if size_mb > cap:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds your per-upload cap of {cap} MB.",
        )


async def check_usage_limit(user_id: str, perms: dict):
    if perms.get("role") == "super_admin":
        return
    limit = perms.get("analysesPerMonth", 10)
    if limit == -1:
        return
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    db = get_db()
    rec = await db.usage_records.find_one({"userId": user_id, "yearMonth": ym})
    used = rec.get("analysesCount", 0) if rec else 0
    if used >= limit:
        raise HTTPException(
            status_code=402,
            detail=f"Monthly analysis limit reached ({used}/{limit}). Contact your administrator to increase your cap.",
        )


async def bump_usage(user_id: str):
    db = get_db()
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    await db.usage_records.update_one(
        {"userId": user_id, "yearMonth": ym},
        {
            "$inc": {"analysesCount": 1},
            "$set": {"updatedAt": datetime.now(timezone.utc).isoformat()},
        },
        upsert=True,
    )


async def audit_log(user_id: str, action: str, resource: str, resource_id: str = "",
                    request: Request | None = None, extra: dict | None = None):
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": f"{user_id[:8]}-{now}-{action}",
        "user_id": user_id,
        "action": action,
        "resource": resource,
        "resource_id": resource_id,
        "ip_address": request.client.host if request and request.client else "",
        "user_agent": request.headers.get("user-agent", "") if request else "",
        "extra": extra or {},
        "timestamp": now,
    }
    await db.audit_log.insert_one(doc)
