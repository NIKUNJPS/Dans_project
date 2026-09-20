"""Project & file cleanup — hard delete and the auto-sweep of unused records.

Two capabilities:

  • hard_delete_project(): permanently removes a project AND everything that belongs
    to it — its uploaded files (disk + DB), analyses, RFIs, estimates that reference its
    files, and any tonnage-lock cache for those files. (The normal DELETE route only
    *archives*; this is the real removal.)

  • sweep_unused(): finds projects that were created but never used (no files, no
    analyses, no RFIs, no estimates) and are older than a grace period, plus orphaned
    uploaded files that were never attached to anything, and either archives or hard-
    deletes them. Runs from the admin endpoint and from the startup background loop.

All timestamps in this app are ISO-8601 strings, so comparisons parse then compare.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _older_than(value, cutoff: datetime) -> bool:
    dt = _parse_iso(value)
    # Missing/unparseable timestamps are treated as OLD (legacy rows) so they can be
    # swept rather than lingering forever.
    return dt is None or dt < cutoff


async def _delete_files_on_disk(db, file_docs: list[dict], upload_dir: str) -> int:
    removed = 0
    for f in file_docs:
        key = f.get("storage_key")
        if not key:
            continue
        try:
            (Path(upload_dir) / key).unlink(missing_ok=True)
            removed += 1
        except OSError as exc:  # noqa: PERF203
            logger.warning("cleanup_disk_unlink_failed key=%s error=%s", key, exc)
    return removed


async def hard_delete_project(db, pid: str, upload_dir: str) -> dict:
    """Permanently remove a project and every record that belongs to it.

    Returns a dict of what was removed. Idempotent: a missing project yields zeros.
    """
    project = await db.projects.find_one({"id": pid})
    if not project:
        return {"project": 0, "files": 0, "analyses": 0, "rfis": 0, "estimates": 0, "tonnage_locks": 0}

    file_docs = await db.files.find({"project_id": pid}).to_list(10_000)
    file_ids = [f["id"] for f in file_docs]

    disk_removed = await _delete_files_on_disk(db, file_docs, upload_dir)

    files_res = await db.files.delete_many({"project_id": pid})
    analyses_res = await db.analyses.delete_many({"project_id": pid})
    rfis_res = await db.rfis.delete_many({"project_id": pid})

    # Estimates & tonnage-locks are keyed by file_ids, not project_id — remove any that
    # reference this project's now-deleted files.
    est_res = tl_res = None
    if file_ids:
        est_res = await db.estimates.delete_many({"file_ids": {"$in": file_ids}})
        tl_res = await db.tonnage_locks.delete_many({"file_ids": {"$in": file_ids}})

    await db.projects.delete_one({"id": pid})

    report = {
        "project": 1,
        "files": files_res.deleted_count,
        "files_on_disk": disk_removed,
        "analyses": analyses_res.deleted_count,
        "rfis": rfis_res.deleted_count,
        "estimates": est_res.deleted_count if est_res else 0,
        "tonnage_locks": tl_res.deleted_count if tl_res else 0,
    }
    logger.info("hard_deleted_project pid=%s report=%s", pid, report)
    return report


async def _referenced_file_ids(db) -> set[str]:
    """All file ids referenced by any analysis or estimate (so they are 'in use')."""
    used: set[str] = set()
    async for a in db.analyses.find({}, {"_id": 0, "file_ids": 1}):
        used.update(a.get("file_ids") or [])
    async for e in db.estimates.find({}, {"_id": 0, "file_ids": 1}):
        used.update(e.get("file_ids") or [])
    return used


async def _project_is_unused(db, pid: str) -> bool:
    """A project is unused when nothing at all hangs off it."""
    if await db.files.count_documents({"project_id": pid}) > 0:
        return False
    if await db.analyses.count_documents({"project_id": pid}) > 0:
        return False
    if await db.rfis.count_documents({"project_id": pid}) > 0:
        return False
    return True


async def sweep_unused(
    db,
    upload_dir: str,
    *,
    grace_hours: int = 72,
    hard: bool = False,
    dry_run: bool = False,
    include_orphan_files: bool = True,
) -> dict:
    """Sweep unused projects and orphaned files.

    grace_hours : only touch records created more than this many hours ago.
    hard        : hard-delete matched projects (else archive them).
    dry_run     : report what WOULD happen without changing anything.
    """
    cutoff = _now() - timedelta(hours=max(0, grace_hours))
    swept_projects: list[dict] = []
    orphan_files_removed = 0

    # ── Unused projects ──────────────────────────────────────────────────────
    async for p in db.projects.find({"status": {"$ne": "archived"}}, {"_id": 0, "id": 1, "name": 1, "created_at": 1}):
        if not _older_than(p.get("created_at"), cutoff):
            continue
        if not await _project_is_unused(db, p["id"]):
            continue
        swept_projects.append({"id": p["id"], "name": p.get("name", ""), "action": "hard_delete" if hard else "archive"})
        if dry_run:
            continue
        if hard:
            await hard_delete_project(db, p["id"], upload_dir)
        else:
            await db.projects.update_one(
                {"id": p["id"]},
                {"$set": {"status": "archived", "archived_by": "auto-sweep", "updated_at": _now().isoformat()}},
            )

    # ── Orphan files (uploaded, never attached to a project, never used) ───────
    if include_orphan_files:
        used = await _referenced_file_ids(db)
        orphan_docs: list[dict] = []
        async for f in db.files.find(
            {"$or": [{"project_id": None}, {"project_id": ""}, {"project_id": {"$exists": False}}]},
            {"_id": 0, "id": 1, "storage_key": 1, "created_at": 1},
        ):
            if f["id"] in used:
                continue
            if not _older_than(f.get("created_at"), cutoff):
                continue
            orphan_docs.append(f)
        if not dry_run and orphan_docs:
            await _delete_files_on_disk(db, orphan_docs, upload_dir)
            await db.files.delete_many({"id": {"$in": [f["id"] for f in orphan_docs]}})
        orphan_files_removed = len(orphan_docs)

    report = {
        "cutoff": cutoff.isoformat(),
        "grace_hours": grace_hours,
        "hard": hard,
        "dry_run": dry_run,
        "projects_swept": len(swept_projects),
        "projects": swept_projects,
        "orphan_files_removed": orphan_files_removed,
    }
    logger.info(
        "sweep_unused projects=%d orphan_files=%d hard=%s dry_run=%s",
        len(swept_projects), orphan_files_removed, hard, dry_run,
    )
    return report
