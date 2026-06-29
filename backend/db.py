"""MongoDB async client and collection accessors."""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from config import settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_url)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        _db = get_client()[settings.db_name]
    return _db


async def ensure_indexes() -> None:
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("role")
    await db.projects.create_index("owner_id")
    await db.projects.create_index("status")
    await db.files.create_index("project_id")
    await db.files.create_index("uploaded_by")
    await db.analyses.create_index([("project_id", 1), ("created_at", -1)])
    await db.analyses.create_index("status")
    await db.analyses.create_index("requested_by")
    await db.rfis.create_index("project_id")
    await db.rfis.create_index([("status", 1), ("created_at", -1)])
    await db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    await db.audit_log.create_index([("timestamp", -1)])
    await db.audit_log.create_index("user_id")
    await db.feature_permissions.create_index("userId", unique=True)
    await db.usage_records.create_index([("userId", 1), ("yearMonth", 1)], unique=True)
    await db.estimates.create_index([("user_id", 1), ("created_at", -1)])
