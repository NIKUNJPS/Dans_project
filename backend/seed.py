"""Seed super-admin account(s) on startup. Idempotent; migrates legacy `admin` role to `super_admin`."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from config import settings
from db import get_db
from security import hash_password, sha256_hex

logger = logging.getLogger(__name__)


async def _upsert_super_admin(email: str, password: str, first: str, last: str) -> None:
    if not (email and password):
        return
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    existing = await db.users.find_one({"email": email.lower()})
    if existing:
        # Migrate legacy `admin` → `super_admin`; refresh other safe fields.
        await db.users.update_one(
            {"email": email.lower()},
            {
                "$set": {
                    "role": "super_admin",
                    "is_verified": True,
                    "is_active": True,
                    "subscription_tier": "enterprise",
                    "updated_at": now,
                }
            },
        )
        logger.info("Seed super_admin · refreshed %s", email)
        return

    uid = sha256_hex(f"admin:{email}:{now}")[:32]
    doc = {
        "id": uid,
        "email": email.lower(),
        "password_hash": hash_password(password),
        "first_name": first,
        "last_name": last,
        "company": "4XStruct Inc.",
        "country": "India",
        "role": "super_admin",
        "phone": "",
        "avatar_url": "",
        "is_verified": True,
        "is_active": True,
        "subscription_tier": "enterprise",
        "created_by": None,
        "usage_this_month": {"analyses": 0, "files_processed": 0, "total_file_size_mb": 0},
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(doc)
    logger.info("Seed super_admin · created %s", email)


async def _migrate_legacy_roles() -> None:
    """One-shot migration: anyone still on the deprecated roles becomes a detailer
    (so their historical records remain attributable to a valid 3-role account)."""
    db = get_db()
    deprecated = ["estimator", "engineer", "pm", "modular", "admin"]
    # `admin` is migrated separately via _upsert_super_admin for the seeded accounts.
    # Anything else with role=admin (manually elevated) → super_admin.
    res_admin = await db.users.update_many(
        {"role": "admin"},
        {"$set": {"role": "super_admin", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if res_admin.modified_count:
        logger.info("Migrated %d legacy `admin` accounts → super_admin", res_admin.modified_count)
    res = await db.users.update_many(
        {"role": {"$in": deprecated[:-1]}},  # exclude admin (already handled)
        {"$set": {"role": "detailer", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if res.modified_count:
        logger.info("Migrated %d legacy role users → detailer", res.modified_count)


async def seed_admin() -> None:
    await _migrate_legacy_roles()
    await _upsert_super_admin(
        settings.admin_email,
        settings.admin_password,
        settings.admin_first_name,
        settings.admin_last_name,
    )
    await _upsert_super_admin(
        os.environ.get("ADMIN_EMAIL_2", ""),
        os.environ.get("ADMIN_PASSWORD_2", ""),
        os.environ.get("ADMIN_FIRST_NAME_2", "Admin"),
        os.environ.get("ADMIN_LAST_NAME_2", "User"),
    )
