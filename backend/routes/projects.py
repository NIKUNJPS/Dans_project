"""Project CRUD routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from cleanup import hard_delete_project, sweep_unused
from config import settings
from db import get_db
from middleware.permission_guard import audit_log
from models import ProjectCreate, ProjectUpdate
from security import block_write_if_readonly, get_current_user, get_super_admin, sha256_hex

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _project_out(db, p: dict) -> dict:
    owner = await db.users.find_one({"id": p["owner_id"]}, {"_id": 0, "first_name": 1, "last_name": 1, "email": 1})
    file_count = await db.files.count_documents({"project_id": p["id"]})
    analysis_count = await db.analyses.count_documents({"project_id": p["id"]})
    rfi_count = await db.rfis.count_documents({"project_id": p["id"]})
    p["owner_name"] = (
        f"{owner.get('first_name','')} {owner.get('last_name','')}".strip()
        if owner else ""
    )
    p["file_count"] = file_count
    p["analysis_count"] = analysis_count
    p["rfi_count"] = rfi_count
    return p


@router.get("")
async def list_projects(user=Depends(get_current_user)):
    db = get_db()
    q = {}
    if user["role"] != "super_admin":
        q = {
            "$or": [
                {"owner_id": user["id"]},
                {"team_members.user_id": user["id"]},
            ]
        }
    items = await db.projects.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    out = []
    for p in items:
        out.append(await _project_out(db, p))
    return {"items": out, "total": len(out)}


@router.post("")
async def create_project(data: ProjectCreate, user=Depends(get_current_user)):
    db = get_db()
    now = _now()
    pid = sha256_hex(f"project:{user['id']}:{data.name}:{now}")[:24]
    doc = {
        "id": pid,
        "name": data.name,
        "description": data.description,
        "owner_id": user["id"],
        "team_members": [],
        "status": "active",
        "tags": data.tags,
        "created_at": now,
        "updated_at": now,
    }
    await db.projects.insert_one(doc)
    doc.pop("_id", None)
    return await _project_out(db, doc)


@router.get("/{pid}")
async def get_project(pid: str, user=Depends(get_current_user)):
    db = get_db()
    p = await db.projects.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if user["role"] != "super_admin" and p["owner_id"] != user["id"] and not any(
        t.get("user_id") == user["id"] for t in p.get("team_members", [])
    ):
        raise HTTPException(status_code=403, detail="Access denied")
    return await _project_out(db, p)


@router.put("/{pid}")
async def update_project(pid: str, data: ProjectUpdate, user=Depends(get_current_user)):
    db = get_db()
    p = await db.projects.find_one({"id": pid})
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if user["role"] != "super_admin" and p["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can update")
    updates = {k: v for k, v in data.model_dump(exclude_none=True).items()}
    updates["updated_at"] = _now()
    await db.projects.update_one({"id": pid}, {"$set": updates})
    fresh = await db.projects.find_one({"id": pid}, {"_id": 0})
    return await _project_out(db, fresh)


@router.delete("/{pid}")
async def delete_project(
    pid: str,
    request: Request,
    hard: bool = Query(False, description="Permanently remove the project and all its files/analyses/estimates."),
    user=Depends(get_current_user),
):
    """Archive a project (default), or permanently remove it with ?hard=true.

    A hard delete removes the project plus its uploaded files (disk + DB), analyses,
    RFIs, estimates referencing its files, and cached tonnage locks — it cannot be undone.
    """
    block_write_if_readonly(user)
    db = get_db()
    p = await db.projects.find_one({"id": pid})
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if user["role"] != "super_admin" and p["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can delete")

    if hard:
        report = await hard_delete_project(db, pid, settings.upload_dir)
        await audit_log(user["id"], "project.hard_delete", "project", pid, request, extra=report)
        return {"message": "Project permanently deleted", "removed": report}

    await db.projects.update_one({"id": pid}, {"$set": {"status": "archived", "updated_at": _now()}})
    await audit_log(user["id"], "project.archive", "project", pid, request)
    return {"message": "Project archived"}


@router.post("/maintenance/sweep")
async def run_sweep(
    request: Request,
    grace_hours: int = Query(72, ge=0, description="Only touch records older than this many hours."),
    hard: bool = Query(False, description="Hard-delete unused projects instead of archiving them."),
    dry_run: bool = Query(False, description="Report what would be removed without changing anything."),
    user=Depends(get_super_admin),
):
    """Super-admin: sweep unused/empty projects and orphaned files.

    Use ?dry_run=true first to preview. The startup background loop calls the same
    routine on a schedule (see AUTO_SWEEP_* env vars)."""
    db = get_db()
    report = await sweep_unused(
        db, settings.upload_dir, grace_hours=grace_hours, hard=hard, dry_run=dry_run,
    )
    if not dry_run:
        await audit_log(user["id"], "project.sweep", "maintenance", "sweep", request, extra={
            "projects_swept": report["projects_swept"],
            "orphan_files_removed": report["orphan_files_removed"],
            "hard": hard,
        })
    return report


@router.post("/{pid}/team")
async def add_team_member(pid: str, payload: dict, user=Depends(get_current_user)):
    db = get_db()
    p = await db.projects.find_one({"id": pid})
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if user["role"] != "super_admin" and p["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can add members")

    email = (payload.get("email") or "").lower()
    role = payload.get("role", "detailer")
    target = await db.users.find_one({"email": email})
    if not target:
        raise HTTPException(status_code=404, detail="User with this email not found")
    member = {
        "user_id": target["id"],
        "email": target["email"],
        "name": f"{target.get('first_name','')} {target.get('last_name','')}".strip(),
        "role": role,
        "added_at": _now(),
    }
    await db.projects.update_one(
        {"id": pid},
        {
            "$pull": {"team_members": {"user_id": target["id"]}},
        },
    )
    await db.projects.update_one(
        {"id": pid},
        {
            "$push": {"team_members": member},
            "$set": {"updated_at": _now()},
        },
    )
    return member
