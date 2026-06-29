"""RFI Tracker routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from db import get_db
from models import RfiCreate, RfiUpdate
from security import get_current_user, sha256_hex

router = APIRouter(prefix="/api/rfis", tags=["rfis"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _next_rfi_number(db, project_id: str | None) -> str:
    count = await db.rfis.count_documents({"project_id": project_id} if project_id else {})
    return f"RFI-{(count + 1):04d}"


async def _enrich(db, r: dict) -> dict:
    if r.get("project_id"):
        p = await db.projects.find_one({"id": r["project_id"]}, {"_id": 0, "name": 1})
        r["project_name"] = p["name"] if p else None
    creator = await db.users.find_one({"id": r["created_by"]}, {"_id": 0, "first_name": 1, "last_name": 1})
    if creator:
        r["created_by_name"] = f"{creator.get('first_name','')} {creator.get('last_name','')}".strip()
    return r


@router.get("")
async def list_rfis(project_id: str | None = None, status: str | None = None, user=Depends(get_current_user)):
    db = get_db()
    q: dict = {}
    if project_id:
        q["project_id"] = project_id
    if status:
        q["status"] = status
    if user["role"] != "super_admin":
        q["$or"] = [{"created_by": user["id"]}, {"assigned_to": user["id"]}]
    items = await db.rfis.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    for r in items:
        await _enrich(db, r)
    return {"items": items, "total": len(items)}


@router.post("")
async def create_rfi(data: RfiCreate, user=Depends(get_current_user)):
    db = get_db()
    now = _now()
    rid = sha256_hex(f"rfi:{user['id']}:{data.subject}:{now}")[:24]
    number = await _next_rfi_number(db, data.project_id)
    doc = {
        "id": rid,
        "project_id": data.project_id,
        "analysis_id": data.analysis_id,
        "rfi_number": number,
        "subject": data.subject,
        "body": data.body,
        "priority": data.priority,
        "status": "draft",
        "assigned_to": data.assigned_to,
        "response": "",
        "sheet_reference": data.sheet_reference,
        "blocking": data.blocking,
        "created_by": user["id"],
        "created_at": now,
        "responded_at": None,
    }
    await db.rfis.insert_one(doc)
    doc.pop("_id", None)
    return await _enrich(db, doc)


@router.get("/{rid}")
async def get_rfi(rid: str, user=Depends(get_current_user)):
    db = get_db()
    r = await db.rfis.find_one({"id": rid}, {"_id": 0})
    if not r:
        raise HTTPException(status_code=404, detail="RFI not found")
    return await _enrich(db, r)


@router.put("/{rid}")
async def update_rfi(rid: str, data: RfiUpdate, user=Depends(get_current_user)):
    db = get_db()
    r = await db.rfis.find_one({"id": rid})
    if not r:
        raise HTTPException(status_code=404, detail="RFI not found")
    if user["role"] != "super_admin" and r["created_by"] != user["id"] and r.get("assigned_to") != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    updates = {k: v for k, v in data.model_dump(exclude_none=True).items()}
    if updates.get("status") == "responded" and r.get("status") != "responded":
        updates["responded_at"] = _now()
    if updates:
        await db.rfis.update_one({"id": rid}, {"$set": updates})
    fresh = await db.rfis.find_one({"id": rid}, {"_id": 0})
    return await _enrich(db, fresh)


@router.delete("/{rid}")
async def delete_rfi(rid: str, user=Depends(get_current_user)):
    db = get_db()
    r = await db.rfis.find_one({"id": rid})
    if not r:
        raise HTTPException(status_code=404, detail="RFI not found")
    if user["role"] != "super_admin" and r["created_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only creator can delete")
    await db.rfis.delete_one({"id": rid})
    return {"message": "RFI deleted"}
