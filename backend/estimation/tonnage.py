"""Project take-off lock — one take-off per drawing set, shared by every consumer.

The MTO Engine, Fabricator Estimation Pro and the AI estimate page all price the SAME
take-off for the same files, so they can never report different tonnages. The cache is
keyed by the drawing set (sorted file ids), not by an analysis. Running the MTO Engine
(or re-running it) refreshes the lock; the estimators reuse the latest lock when one
exists and create it otherwise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from db import get_db
from security import sha256_hex

logger = logging.getLogger(__name__)

# Bump when the take-off schema changes so stale locks are recomputed, not mis-read.
TAKEOFF_VERSION = 2


def lock_key(file_ids: list[str]) -> str:
    return sha256_hex("|".join(sorted(file_ids)))


class TakeoffCache:
    """Async get/put used by pipeline.run_mode()."""

    def __init__(self, file_ids: list[str]):
        self.file_ids = sorted(file_ids or [])
        self.key = lock_key(self.file_ids)

    async def get(self) -> dict | None:
        if not self.file_ids:
            return None
        doc = await get_db().tonnage_locks.find_one({"id": self.key}, {"_id": 0})
        if doc and doc.get("version") == TAKEOFF_VERSION and doc.get("takeoff"):
            return doc["takeoff"]
        return None

    async def put(self, takeoff: dict) -> None:
        if not self.file_ids:
            return
        doc = {
            "id": self.key,
            "version": TAKEOFF_VERSION,
            "file_ids": self.file_ids,
            "tonnage": takeoff.get("totals", {}).get("gross_t", 0),
            "takeoff": takeoff,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await get_db().tonnage_locks.update_one({"id": self.key}, {"$set": doc}, upsert=True)
            logger.info("takeoff_locked key=%s t=%.3f", self.key, doc["tonnage"])
        except Exception as exc:  # noqa: BLE001 — a cache write must never fail the analysis
            logger.warning("takeoff_lock_write_failed key=%s error=%s", self.key, exc)
