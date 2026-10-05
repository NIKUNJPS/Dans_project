"""Analysis routes: queue, status, list, export, rerun, result.

Uses prompts/prompt_router for role-aware prompt composition and middleware/permission_guard
for granular feature gating (mode access, monthly cap, export format, file size, audit log).
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from config import settings
from db import get_db
from export_service import EXPORT_DIR, EXTENSIONS, generate_all_exports, generate_export
from file_prep import prepare_file
from claude_engine import has_credentials
from estimation.tonnage import TakeoffCache
from pipeline import Cancelled, run_mode
from middleware.permission_guard import (
    audit_log,
    bump_usage,
    check_export_access,
    check_feature,
    check_mode_access,
    check_usage_limit,
    load_permissions,
)
from models import AnalysisCreate
from prompts.prompt_router import (
    get_mode_meta,
    get_mode_prompt,
    get_system_prompt,
    list_modes_for_role,
)
from prompts.shared_rules import GLOBAL_FORMAT_RULES
from security import block_write_if_readonly, get_current_user, sha256_hex

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analyses", tags=["analyses"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_JURISDICTION_BY_COUNTRY = {"USA": "USA", "US": "USA", "CANADA": "CANADA", "AUSTRALIA": "AUSTRALIA"}


async def _run_analysis_task(analysis_id: str):
    """Background task: run the binder pipeline, store ONE report, then build exports."""
    db = get_db()
    started = time.time()
    analysis = await db.analyses.find_one({"id": analysis_id})
    if not analysis:
        logger.error("Analysis %s not found", analysis_id)
        return

    mode_id = analysis["mode"]
    meta = get_mode_meta(mode_id)
    if not meta:
        await db.analyses.update_one(
            {"id": analysis_id},
            {"$set": {"status": "failed", "error_message": f"Unknown mode {mode_id}", "completed_at": _now_iso()}},
        )
        return

    await db.analyses.update_one(
        {"id": analysis_id},
        {"$set": {"status": "processing", "stage": "Preparing binder",
                  "progress": {"label": "Preparing binder", "done": 0, "total": 1}}},
    )

    # Convert what the model can't read natively (Excel→CSV, Word→text, NC/DSTV→text);
    # proprietary CAD/BIM binaries are reported back with an "export to PDF" note.
    scratch_dir = Path(tempfile.mkdtemp(prefix=f"prep_{analysis_id[:12]}_"))
    file_docs = []
    if analysis.get("file_ids"):
        file_docs = await db.files.find({"id": {"$in": analysis["file_ids"]}}).to_list(1000)
        order = {fid: i for i, fid in enumerate(analysis["file_ids"])}
        file_docs.sort(key=lambda f: order.get(f["id"], 1_000_000))
    files: list[tuple[str, str, str]] = []
    skipped_files: list[str] = []
    for f in file_docs:
        pair, _status = prepare_file(f, Path(settings.upload_dir), scratch_dir)
        name = f.get("original_name") or f.get("storage_key") or "file"
        if pair:
            files.append((pair[0], pair[1], name))
        else:
            skipped_files.append(name)

    async def progress(label: str, done: int, total: int) -> None:
        await db.analyses.update_one(
            {"id": analysis_id},
            {"$set": {"stage": label, "progress": {"label": label, "done": done, "total": total}}},
        )

    async def cancelled() -> bool:
        return await db.analyses.count_documents({"id": analysis_id}, limit=1) == 0

    try:
        if not has_credentials():
            raise RuntimeError("No LLM credentials configured. Set ANTHROPIC_API_KEY in the environment.")
        if file_docs and not files:
            raise RuntimeError(
                "None of the uploaded files could be read (" + ", ".join(skipped_files) + "). PDF, images, "
                "Excel, Word and text/fabrication files are supported; proprietary CAD/BIM (.dwg/.dxf/.rvt/"
                ".nwd/.ifc) must be exported to PDF first."
            )

        requester = await db.users.find_one({"id": analysis["requested_by"]}, {"_id": 0, "role": 1, "country": 1})
        requester_role = (requester or {}).get("role", "detailer")
        jurisdiction = _JURISDICTION_BY_COUNTRY.get(str((requester or {}).get("country") or "USA").upper(), "USA")

        system_persona = get_system_prompt(requester_role)
        mode_prompt = get_mode_prompt(requester_role, mode_id)
        composed_system_prompt = (
            f"{system_persona.strip()}\n\n"
            f"## MODE: {meta['label']}\n{mode_prompt}\n\n"
            f"## GLOBAL FORMATTING\n{GLOBAL_FORMAT_RULES}"
        )
        user_text = (analysis.get("input_text") or "").strip()
        if skipped_files:
            user_text += (
                "\n\nNOTE: these uploads are proprietary CAD/BIM binaries and were not readable; "
                "export them to PDF to include them: " + ", ".join(skipped_files) + "."
            )

        result = await run_mode(
            mode_id=mode_id,
            system_prompt=composed_system_prompt,
            user_text=user_text,
            files=files,
            skipped=skipped_files,
            progress=progress,
            cancelled=cancelled,
            label=analysis_id,
            jurisdiction_hint=jurisdiction,
            takeoff_cache=TakeoffCache(analysis.get("file_ids") or []),
        )
        output, model_used = result.markdown, result.engine
    except Cancelled:
        logger.info("analysis_cancelled id=%s (deleted while running)", analysis_id)
        return
    except Exception as e:  # noqa: BLE001
        logger.exception("Analysis failed")
        await db.analyses.update_one(
            {"id": analysis_id},
            {"$set": {"status": "failed", "stage": "failed", "error_message": str(e), "completed_at": _now_iso()}},
        )
        return
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    elapsed = round(time.time() - started, 2)
    output_hash = sha256_hex(output)

    # Issue counts from the report's own priority labels.
    up = output.upper()
    crit = len(re.findall(r"\bCRITICAL\b", up))
    major = len(re.findall(r"\b(?:MAJOR|URGENT)\b", up))
    minor = len(re.findall(r"\b(?:MINOR|STANDARD|OBSERVATION)\b", up))
    issues = {"critical": crit, "major": major, "minor": minor, "total": crit + major + minor}
    quality = max(0, min(100, 100 - crit * 8 - major * 3 - minor * 1))

    # Persist the report and mark COMPLETE first — exports are best-effort afterwards.
    res = await db.analyses.update_one(
        {"id": analysis_id},
        {
            "$set": {
                "status": "complete",
                "stage": "complete",
                "progress": {"label": "Complete", "done": 1, "total": 1},
                "output_markdown": output,
                "model_used": model_used,
                "processing_time_seconds": elapsed,
                "issues_found": issues,
                "quality_score": quality,
                "blockchain_hash": output_hash,
                "binder": result.meta.get("binder"),
                "usage": result.meta.get("usage"),
                "takeoff_summary": result.meta.get("takeoff_summary"),
                "estimate_summary": result.meta.get("estimate"),
                "exports": [],
                "completed_at": _now_iso(),
            }
        },
    )
    if res.matched_count == 0:
        return  # deleted while the report was being written

    user_id = analysis["requested_by"]
    await db.notifications.insert_one(
        {
            "id": sha256_hex(f"notif:{analysis_id}:{_now_iso()}"),
            "user_id": user_id,
            "type": "analysis_complete",
            "title": "Analysis complete",
            "message": f"{meta['label']} completed in {elapsed}s",
            "is_read": False,
            "action_url": f"/analyses/{analysis_id}",
            "created_at": _now_iso(),
        }
    )
    await db.users.update_one({"id": user_id}, {"$inc": {"usage_this_month.analyses": 1}})

    try:
        export_meta = await _export_meta(db, {**analysis, "completed_at": _now_iso(), "model_used": model_used,
                                              "blockchain_hash": output_hash, "mode_label": meta["label"]})
        exports = await asyncio.to_thread(generate_all_exports, output, export_meta)
        await db.analyses.update_one(
            {"id": analysis_id},
            {"$set": {"exports": [
                {"format": e["format"], "url": f"/api/analyses/{analysis_id}/export/{e['format']}",
                 "generated_at": _now_iso()}
                for e in exports if e.get("path")
            ]}},
        )
    except Exception:  # noqa: BLE001 — exports are non-critical; the report is already saved
        logger.exception("export_generation_failed analysis=%s", analysis_id)


async def _export_meta(db, a: dict) -> dict:
    proj = await db.projects.find_one({"id": a.get("project_id")}) if a.get("project_id") else None
    return {
        "id": a["id"],
        "mode_label": a.get("mode_label", "Report"),
        "project_name": proj["name"] if proj else (a.get("project_name") or "Quick Analysis"),
        "completed_at": a.get("completed_at", ""),
        "model_used": a.get("model_used", ""),
        "blockchain_hash": a.get("blockchain_hash", ""),
    }


def _remove_exports(aid: str) -> None:
    for ext in ("pdf", "docx", "xlsx", "csv", "md"):
        try:
            (EXPORT_DIR / f"{aid}.{ext}").unlink(missing_ok=True)
        except OSError:
            pass


@router.get("/modes")
async def list_modes(user=Depends(get_current_user), perms: dict = Depends(load_permissions)):
    """Return modes available to the current user, filtered by feature_permissions.

    Locked modes are NOT returned (per spec: hide, do not gray-out).
    Super admin sees every role's modes.
    """
    role = user["role"]
    if role == "super_admin":
        items = list_modes_for_role("super_admin")
    else:
        all_for_role = list_modes_for_role(role)
        allowed = set(perms.get("allowedModes", []))
        wildcard = "*" in allowed
        items = [m for m in all_for_role if wildcard or m["id"] in allowed]

    # Group set is dynamic — derived from the visible modes
    groups: list[str] = []
    for m in items:
        if m["group"] not in groups:
            groups.append(m["group"])
    return {"modes": items, "groups": groups}


@router.post("")
async def create_analysis(
    data: AnalysisCreate,
    background: BackgroundTasks,
    request: Request,
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    block_write_if_readonly(user)
    db = get_db()

    # Validate the mode is defined in the prompt router for this user's role
    meta = get_mode_meta(data.mode)
    if not meta:
        raise HTTPException(status_code=400, detail=f"Unknown analysis mode '{data.mode}'")

    check_mode_access(perms, data.mode)
    await check_usage_limit(user["id"], perms)

    if perms.get("role") != "super_admin" and not perms.get("canUploadFiles", True) and data.file_ids:
        raise HTTPException(status_code=403, detail="File uploads are disabled for your account.")

    max_files = perms.get("maxFilesPerAnalysis", 3)
    if perms.get("role") != "super_admin" and len(data.file_ids) > max_files:
        raise HTTPException(status_code=413, detail=f"Too many files. Cap is {max_files}.")

    # Project access check
    project_name = "Quick Analysis"
    if data.project_id:
        p = await db.projects.find_one({"id": data.project_id})
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        if user["role"] != "super_admin" and p["owner_id"] != user["id"] and not any(
            t.get("user_id") == user["id"] for t in p.get("team_members", [])
        ):
            raise HTTPException(status_code=403, detail="Access denied to project")
        project_name = p.get("name") or project_name

    full = await db.users.find_one({"id": user["id"]})
    now = _now_iso()
    aid = sha256_hex(f"analysis:{user['id']}:{data.mode}:{now}")[:24]
    doc = {
        "id": aid,
        "project_id": data.project_id,
        "project_name": project_name,
        "file_ids": data.file_ids,
        "requested_by": user["id"],
        "requested_by_name": f"{full.get('first_name','')} {full.get('last_name','')}".strip(),
        "mode": data.mode,
        "mode_label": meta["label"],
        "status": "queued",
        "stage": "queued",
        "model_used": "",
        "input_text": data.input_text,
        "output_markdown": "",
        "processing_time_seconds": 0,
        "issues_found": {"critical": 0, "major": 0, "minor": 0, "total": 0},
        "quality_score": 0,
        "blockchain_hash": "",
        "exports": [],
        "error_message": "",
        "created_at": now,
        "completed_at": None,
    }
    await db.analyses.insert_one(doc)
    await bump_usage(user["id"])
    await audit_log(user["id"], "analysis.create", "analysis", aid, request,
                    extra={"mode": data.mode, "files": len(data.file_ids)})
    background.add_task(_run_analysis_task, aid)
    doc.pop("_id", None)
    return doc


@router.get("")
async def list_analyses(
    project_id: str | None = None,
    quick: bool = False,
    limit: int = 200,
    user=Depends(get_current_user),
):
    """List analyses. `project_id` scopes to one project (owner and team see all of the
    project's runs); `quick=true` lists Quick Analyses (no project)."""
    db = get_db()
    q: dict = {}
    if project_id:
        q["project_id"] = project_id
        if user["role"] != "super_admin":
            p = await db.projects.find_one({"id": project_id})
            if not p or not _can_access_project(p, user):
                raise HTTPException(status_code=403, detail="Access denied to project")
    else:
        if quick:
            q["project_id"] = {"$in": [None, ""]}
        if user["role"] != "super_admin":
            q["requested_by"] = user["id"]
    items = await db.analyses.find(q, {"_id": 0, "output_markdown": 0}).sort("created_at", -1).to_list(min(limit, 1000))
    return {"items": items, "total": len(items)}


def _can_access_project(p: dict, user: dict) -> bool:
    return p.get("owner_id") == user["id"] or any(t.get("user_id") == user["id"] for t in p.get("team_members", []))


async def _can_delete(db, a: dict, user: dict) -> bool:
    """Requester, super admin, or the owner of the analysis's project."""
    if user["role"] == "super_admin" or a.get("requested_by") == user["id"]:
        return True
    if a.get("project_id"):
        p = await db.projects.find_one({"id": a["project_id"]}, {"_id": 0, "owner_id": 1})
        return bool(p and p.get("owner_id") == user["id"])
    return False


@router.post("/bulk-delete")
async def bulk_delete_analyses(payload: dict, request: Request, user=Depends(get_current_user)):
    """Delete analysis history in one call.

    Body (one of):
      {"ids": [...]}                — specific analyses
      {"project_id": "..."}         — every analysis of a project (owner / super admin)
      {"scope": "quick"}            — every Quick Analysis (no project) of the caller
    Report exports on disk are removed too. Running analyses stop at their next batch.
    """
    block_write_if_readonly(user)
    db = get_db()
    ids = [i for i in (payload.get("ids") or []) if isinstance(i, str)]
    project_id = payload.get("project_id")
    scope = payload.get("scope")
    if ids:
        q: dict = {"id": {"$in": ids}}
    elif project_id:
        p = await db.projects.find_one({"id": project_id})
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        if user["role"] != "super_admin" and p.get("owner_id") != user["id"]:
            raise HTTPException(status_code=403, detail="Only the project owner can clear its history")
        q = {"project_id": project_id}
    elif scope == "quick":
        q = {"project_id": {"$in": [None, ""]}, "requested_by": user["id"]}
    else:
        raise HTTPException(status_code=422, detail="Provide ids, project_id, or scope='quick'.")

    deleted: list[str] = []
    async for a in db.analyses.find(q, {"_id": 0, "id": 1, "requested_by": 1, "project_id": 1}):
        if project_id or scope == "quick" or await _can_delete(db, a, user):
            deleted.append(a["id"])
    if deleted:
        await db.analyses.delete_many({"id": {"$in": deleted}})
        for aid in deleted:
            _remove_exports(aid)
    est_deleted = 0
    if project_id and payload.get("include_estimates", True):
        res = await db.estimates.delete_many({"project_id": project_id})
        est_deleted = res.deleted_count
    await audit_log(user["id"], "analysis.bulk_delete", "analysis", project_id or scope or "ids", request,
                    extra={"count": len(deleted), "estimates": est_deleted})
    return {"deleted": len(deleted), "estimates_deleted": est_deleted}


@router.get("/{aid}")
async def get_analysis(aid: str, user=Depends(get_current_user)):
    db = get_db()
    a = await db.analyses.find_one({"id": aid}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if user["role"] != "super_admin" and a["requested_by"] != user["id"]:
        if a.get("project_id"):
            p = await db.projects.find_one({"id": a["project_id"]})
            if not p or (p["owner_id"] != user["id"] and not any(
                t.get("user_id") == user["id"] for t in p.get("team_members", [])
            )):
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")
    return a


@router.get("/{aid}/status")
async def get_status(aid: str, user=Depends(get_current_user)):
    db = get_db()
    a = await db.analyses.find_one(
        {"id": aid},
        {"_id": 0, "status": 1, "stage": 1, "progress": 1, "processing_time_seconds": 1, "model_used": 1,
         "error_message": 1},
    )
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return a


@router.post("/{aid}/rerun")
async def rerun_analysis(
    aid: str,
    background: BackgroundTasks,
    request: Request,
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    block_write_if_readonly(user)
    db = get_db()
    a = await db.analyses.find_one({"id": aid})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if user["role"] != "super_admin" and a["requested_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    check_mode_access(perms, a["mode"])
    await check_usage_limit(user["id"], perms)
    _remove_exports(aid)
    await db.analyses.update_one(
        {"id": aid},
        {
            "$set": {
                "status": "queued",
                "stage": "queued",
                "progress": {"label": "Queued", "done": 0, "total": 1},
                "output_markdown": "",
                "error_message": "",
                "exports": [],
                "completed_at": None,
            }
        },
    )
    await bump_usage(user["id"])
    await audit_log(user["id"], "analysis.rerun", "analysis", aid, request)
    background.add_task(_run_analysis_task, aid)
    return {"message": "Analysis re-queued"}


@router.get("/{aid}/export/{fmt}")
async def download_export(
    aid: str,
    fmt: str,
    request: Request,
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    db = get_db()
    a = await db.analyses.find_one({"id": aid})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if user["role"] != "super_admin" and a["requested_by"] != user["id"]:
        p = await db.projects.find_one({"id": a.get("project_id")}) if a.get("project_id") else None
        if not p or not _can_access_project(p, user):
            raise HTTPException(status_code=403, detail="Access denied")
    if user["role"] != "super_admin":
        check_feature(perms, "canDownloadReports")
    fmt_alias = {"docx": "word", "xlsx": "excel"}.get(fmt, fmt)
    check_export_access(perms, fmt_alias)
    exporter = {"pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "csv": "csv", "markdown": "markdown"}.get(fmt)
    if not exporter:
        raise HTTPException(status_code=400, detail="Unsupported format")
    ext = EXTENSIONS[exporter]
    path = EXPORT_DIR / f"{aid}.{ext}"

    # Build this one format on demand if it is not on disk yet. Exports are produced as a
    # best-effort step AFTER the report is saved, so a download can race ahead of them, a
    # prior build may have failed, or the export folder may have been reset on redeploy.
    if not path.exists():
        if a.get("status") != "complete" or not a.get("output_markdown"):
            raise HTTPException(status_code=409, detail="The report is still processing. Try again when it completes.")
        export_meta = await _export_meta(db, a)
        try:
            await asyncio.to_thread(generate_export, exporter, a["output_markdown"], export_meta)
        except Exception as e:  # noqa: BLE001
            logger.exception("on_demand_export_failed analysis=%s fmt=%s", aid, fmt)
            raise HTTPException(
                status_code=500,
                detail=f"The {fmt.upper()} export could not be built ({type(e).__name__}). Please try again.",
            )
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "md": "text/markdown",
    }
    filename = f"{a.get('mode_label','report').replace(' ', '_')}.{ext}"
    await audit_log(user["id"], "analysis.export", "analysis", aid, request, extra={"format": fmt})
    return FileResponse(str(path), filename=filename, media_type=media.get(ext, "application/octet-stream"))


@router.delete("/{aid}")
async def delete_analysis(
    aid: str,
    request: Request,
    user=Depends(get_current_user),
):
    block_write_if_readonly(user)
    db = get_db()
    a = await db.analyses.find_one({"id": aid})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if not await _can_delete(db, a, user):
        raise HTTPException(status_code=403, detail="Access denied")
    await db.analyses.delete_one({"id": aid})
    _remove_exports(aid)
    await audit_log(user["id"], "analysis.delete", "analysis", aid, request)
    return {"message": "Analysis deleted"}
