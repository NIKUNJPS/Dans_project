"""Project CRUD routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import get_db
from models import ProjectCreate, ProjectUpdate
from security import get_current_user, sha256_hex

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
async def delete_project(pid: str, user=Depends(get_current_user)):
    db = get_db()
    p = await db.projects.find_one({"id": pid})
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if user["role"] != "super_admin" and p["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can delete")
    await db.projects.update_one({"id": pid}, {"$set": {"status": "archived", "updated_at": _now()}})
    return {"message": "Project archived"}


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
