"""Project tonnage lock.

The tonnage for a drawing-set is extracted ONCE and cached, so every consumer —
Master Intake, the MTO Engine and Estimation — reports the identical figure.

The cache is keyed by the drawing-set (the sorted file ids), not by an individual
analysis, so two different modes run against the same files share one locked value.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from db import get_db
from security import sha256_hex
from estimation.ai_extract import extract_quantities

logger = logging.getLogger(__name__)


def _key(file_ids: list[str]) -> str:
    return sha256_hex("|".join(sorted(file_ids)))


async def get_or_lock_tonnage(
    file_ids: list[str],
    file_pairs: list[tuple[str, str]],
    session_id: str,
) -> dict | None:
    """Return the locked tonnage for this drawing-set, extracting + caching once.

    Returns ``{"tonnage": float, "extracted": dict, "engine": str}`` or ``None`` when
    no usable tonnage could be determined (e.g. detailing-only packages).
    """
    if not file_ids or not file_pairs:
        return None

    db = get_db()
    key = _key(file_ids)

    existing = await db.tonnage_locks.find_one({"id": key})
    if existing and existing.get("tonnage"):
        return {
            "tonnage": existing["tonnage"],
            "extracted": existing.get("extracted", {}),
            "engine": existing.get("engine", "STRUCTMIND CORE"),
        }

    try:
        extracted, engine = await extract_quantities(
            role="fabricator", session_id=session_id, file_paths=file_pairs
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tonnage_lock_extract_failed key=%s error=%s", key, exc)
        return None

    tonnage = float(extracted.get("tonnage", 0) or 0)
    if tonnage <= 0:
        return None

    doc = {
        "id": key,
        "tonnage": round(tonnage, 2),
        "extracted": extracted,
        "engine": engine,
        "file_ids": sorted(file_ids),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.tonnage_locks.update_one({"id": key}, {"$set": doc}, upsert=True)
    logger.info("tonnage_locked key=%s tonnage=%.2f", key, doc["tonnage"])
    return {"tonnage": doc["tonnage"], "extracted": extracted, "engine": engine}
