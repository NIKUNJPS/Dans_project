"""File upload & download routes (local storage). Wired to feature_permissions (file size cap)."""
from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from config import settings
from db import get_db
from middleware.permission_guard import (
    audit_log,
    check_feature,
    check_file_size,
    load_permissions,
)
from security import block_write_if_readonly, get_current_user, sha256_hex

router = APIRouter(prefix="/api/files", tags=["files"])

ALLOWED_EXT = {
    ".pdf", ".dwg", ".dxf", ".ifc", ".rvt", ".nwd", ".nc1", ".dstv",
    ".xlsx", ".xls", ".csv", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg",
}
CHUNK = 1024 * 1024  # 1 MB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    block_write_if_readonly(user)
    if user["role"] != "super_admin":
        check_feature(perms, "canUploadFiles")
    db = get_db()
    ext = Path(file.filename or "file").suffix.lower()
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(status_code=415, detail=f"File type {ext} not allowed")

    now = _now()
    fid = sha256_hex(f"{user['id']}{file.filename}{now}")[:32]
    storage_name = f"{fid}{ext or ''}"
    dest = Path(settings.upload_dir) / storage_name

    size = 0
    cap_mb = perms.get("maxFileSizeMb", 25) if user["role"] != "super_admin" else settings.max_upload_mb
    max_bytes = cap_mb * 1024 * 1024

    sha = hashlib.sha256()
    with open(dest, "wb") as out:
        while True:
            buf = await file.read(CHUNK)
            if not buf:
                break
            size += len(buf)
            if size > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds your per-upload cap of {cap_mb} MB.",
                )
            sha.update(buf)
            out.write(buf)
    file_hash = sha.hexdigest()
    size_mb = round(size / (1024 * 1024), 2)
    check_file_size(perms, size_mb)  # double-check post-write
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"

    # Project access check
    if project_id:
        p = await db.projects.find_one({"id": project_id})
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        if user["role"] != "super_admin" and p["owner_id"] != user["id"] and not any(
            t.get("user_id") == user["id"] for t in p.get("team_members", [])
        ):
            raise HTTPException(status_code=403, detail="Access denied to project")

    doc = {
        "id": fid,
        "project_id": project_id,
        "uploaded_by": user["id"],
        "original_name": file.filename or storage_name,
        "storage_key": storage_name,
        "mime_type": mime,
        "size_bytes": size,
        "size_mb": size_mb,
        "is_chunked": size > 8 * 1024 * 1024,
        "processing_status": "ready",
        "metadata": {},
        "blockchain_hash": file_hash,
        "created_at": now,
    }
    await db.files.insert_one(doc)
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$inc": {
                "usage_this_month.files_processed": 1,
                "usage_this_month.total_file_size_mb": doc["size_mb"],
            }
        },
    )
    await audit_log(user["id"], "file.upload", "file", fid, request,
                    extra={"name": doc["original_name"], "size_mb": size_mb})
    doc.pop("_id", None)
    return doc


@router.get("/{fid}")
async def get_file_meta(fid: str, user=Depends(get_current_user)):
    db = get_db()
    f = await db.files.find_one({"id": fid}, {"_id": 0})
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return f


@router.get("/{fid}/download")
async def download_file(fid: str, user=Depends(get_current_user)):
    db = get_db()
    f = await db.files.find_one({"id": fid})
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    if user["role"] != "super_admin" and f["uploaded_by"] != user["id"]:
        if f.get("project_id"):
            p = await db.projects.find_one({"id": f["project_id"]})
            if not p or (p["owner_id"] != user["id"] and not any(
                t.get("user_id") == user["id"] for t in p.get("team_members", [])
            )):
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")
    path = Path(settings.upload_dir) / f["storage_key"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(str(path), filename=f["original_name"], media_type=f.get("mime_type", "application/octet-stream"))


@router.delete("/{fid}")
async def delete_file(fid: str, request: Request, user=Depends(get_current_user)):
    block_write_if_readonly(user)
    db = get_db()
    f = await db.files.find_one({"id": fid})
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    if user["role"] != "super_admin" and f["uploaded_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        (Path(settings.upload_dir) / f["storage_key"]).unlink(missing_ok=True)
    except Exception:
        pass
    await db.files.delete_one({"id": fid})
    await audit_log(user["id"], "file.delete", "file", fid, request)
    return {"message": "File deleted"}


@router.get("")
async def list_files(project_id: str | None = None, user=Depends(get_current_user)):
    db = get_db()
    q = {}
    if project_id:
        q["project_id"] = project_id
    else:
        q["uploaded_by"] = user["id"]
    items = await db.files.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"items": items}
