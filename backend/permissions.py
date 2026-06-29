"""Feature-permissions document — per-user granular access matrix (managed by super_admin).

Plan A defaults (locked-down): super_admin must explicitly enable each capability for
a new Detailer / Fabricator. Defaults below are intentionally restrictive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db import get_db


# Single source of truth for default permission shape for newly-created users.
def default_permissions(role: str) -> dict[str, Any]:
    return {
        "analysesPerMonth":    10,
        "maxFileSizeMb":       100,
        "maxFilesPerAnalysis": 40,           # multi-drawing sets: batched 8-per-batch upstream
        "allowedModes":        [],            # super_admin enables each mode explicitly
        "allowedExports":      ["markdown", "pdf"],
        "canUploadFiles":      True,
        "canCreateProjects":   True,
        "canViewHistory":      True,
        "canDownloadReports":  True,
        "canSendRfis":         False,
        "canViewAuditLog":     False,
        "blockchainAnchoring": False,
        "canRunEstimation":    role == "fabricator",   # detailers must be explicitly opted-in
        "estimationCountries": ["USA"],
        "showDashboardStats":  True,
        "showActivityChart":   False,
    }


def super_admin_permissions(user_id: str) -> dict[str, Any]:
    """Open-everything bundle for super_admin (never persisted; resolved on the fly)."""
    return {
        "userId": user_id,
        "role": "super_admin",
        "analysesPerMonth": -1,
        "maxFileSizeMb": 500,
        "maxFilesPerAnalysis": 500,
        "allowedModes": ["*"],
        "allowedExports": ["pdf", "word", "excel", "csv", "markdown", "json"],
        "canUploadFiles": True,
        "canCreateProjects": True,
        "canViewHistory": True,
        "canDownloadReports": True,
        "canSendRfis": True,
        "canViewAuditLog": True,
        "blockchainAnchoring": True,
        "canRunEstimation": True,
        "estimationCountries": [
            "USA", "Canada", "UK", "UAE", "Australia", "India",
            "Europe", "Saudi Arabia", "Singapore",
        ],
        "showDashboardStats": True,
        "showActivityChart": True,
    }


async def get_permissions(user_id: str, role: str) -> dict[str, Any]:
    """Fetch (or auto-create with defaults) the feature_permissions document for a user."""
    db = get_db()

    if role == "super_admin":
        return super_admin_permissions(user_id)

    doc = await db.feature_permissions.find_one({"userId": user_id}, {"_id": 0})
    if doc:
        # Ensure `role` is always present
        doc["role"] = role
        return doc

    # Persist default permissions on first access
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "userId": user_id,
        "role": role,
        **default_permissions(role),
        "lastUpdatedBy": None,
        "updatedAt": now,
        "createdAt": now,
    }
    await db.feature_permissions.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def update_permissions(user_id: str, updates: dict, admin_id: str) -> dict:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    updates = {k: v for k, v in updates.items() if v is not None}
    updates["lastUpdatedBy"] = admin_id
    updates["updatedAt"] = now
    await db.feature_permissions.update_one(
        {"userId": user_id}, {"$set": updates}, upsert=True,
    )
    fresh = await db.feature_permissions.find_one({"userId": user_id}, {"_id": 0})
    return fresh or {}


def mode_allowed(perms: dict, mode_id: str) -> bool:
    allowed = perms.get("allowedModes", [])
    return "*" in allowed or mode_id in allowed


def export_allowed(perms: dict, fmt: str) -> bool:
    allowed = perms.get("allowedExports", [])
    return "*" in allowed or fmt in allowed
