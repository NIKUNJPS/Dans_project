"""Analysis routes: queue, status, list, export, rerun, result.

Uses prompts/prompt_router for role-aware prompt composition and middleware/permission_guard
for granular feature gating (mode access, monthly cap, export format, file size, audit log).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from config import settings
from db import get_db
from export_service import EXPORT_DIR, generate_all_exports
from gemini_service import run_analysis
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


async def _run_analysis_task(analysis_id: str):
    """Background task to call STRUCTMIND CORE, update analysis, generate exports."""
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
        {"id": analysis_id}, {"$set": {"status": "processing", "stage": "analyzing"}}
    )

    # Collect files
    file_docs = []
    if analysis.get("file_ids"):
        file_docs = await db.files.find({"id": {"$in": analysis["file_ids"]}}).to_list(1000)
    file_pairs: list[tuple[str, str]] = []
    for f in file_docs:
        path = Path(settings.upload_dir) / f["storage_key"]
        if path.exists() and f.get("mime_type"):
            mt = f["mime_type"]
            if mt in {
                "application/pdf",
                "text/plain",
                "text/csv",
                "image/png",
                "image/jpeg",
                "image/webp",
            }:
                file_pairs.append((str(path), mt))

    try:
        # Support both service account (GOOGLE_SERVICE_ACCOUNT_JSON) and API key (llm_key)
        has_service_account = bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
        has_api_key = bool(settings.llm_key)
        if not has_service_account and not has_api_key:
            raise RuntimeError(
                "No LLM credentials configured. "
                "Set GOOGLE_SERVICE_ACCOUNT_JSON or GEMINI_API_KEY in environment."
            )

        requester = await db.users.find_one(
            {"id": analysis["requested_by"]}, {"_id": 0, "role": 1}
        )
        requester_role = (requester or {}).get("role", "detailer")

        # Tonnage is owned by ONE mode only — the MTO Engine — which computes it from its
        # own detailed member-by-member take-off. We deliberately inject NO external
        # tonnage figure: a second (rougher) estimate only ever conflicted with the MTO's
        # detailed total. Master Intake discloses no tonnage at all (handled in its prompt).
        system_persona = get_system_prompt(requester_role)
        mode_prompt = get_mode_prompt(requester_role, mode_id)
        composed_system_prompt = (
            f"{system_persona.strip()}\n\n"
            f"## MODE: {meta['label']}\n{mode_prompt}\n\n"
            f"## GLOBAL FORMATTING\n{GLOBAL_FORMAT_RULES}"
        )

        user_text = analysis.get("input_text") or ""
        if not user_text.strip():
            user_text = (
                f"Run the {meta['label']} mode on the attached drawings / inputs. "
                f"Follow every instruction in the mode prompt verbatim."
            )

        # Estimation engines produce one locked, internally-consistent manifest
        # (tonnage/hours → cost). They must NOT be split across batches and merged, or
        # the headline figures would disagree. Run them in a single coherent pass.
        single_pass = mode_id in {"ESTIMATION_PRO", "FABRICATOR_ESTIMATION_PRO"}

        output, model_used = await run_analysis(
            session_id=analysis_id,
            system_prompt=composed_system_prompt,
            user_text=user_text,
            file_paths=file_pairs,
            single_pass=single_pass,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Analysis failed")
        await db.analyses.update_one(
            {"id": analysis_id},
            {
                "$set": {
                    "status": "failed",
                    "error_message": str(e),
                    "completed_at": _now_iso(),
                }
            },
        )
        return

    elapsed = round(time.time() - started, 2)
    output_hash = sha256_hex(output)

    # Heuristic issue counting
    crit = output.upper().count("CRITICAL")
    major = output.upper().count("MAJOR")
    minor = output.upper().count("MINOR") + output.upper().count("OBSERVATION")
    issues = {"critical": crit, "major": major, "minor": minor, "total": crit + major + minor}
    quality = max(0, min(100, 100 - crit * 8 - major * 3 - minor * 1))

    # Persist the result and mark COMPLETE *first*. The report must never be lost and
    # the UI must update the instant the analysis finishes — even if export rendering is
    # slow or fails. Exports are generated afterwards as a best-effort step (below).
    await db.analyses.update_one(
        {"id": analysis_id},
        {
            "$set": {
                "status": "complete",
                "stage": "complete",
                "output_markdown": output,
                "model_used": model_used,
                "processing_time_seconds": elapsed,
                "issues_found": issues,
                "quality_score": quality,
                "blockchain_hash": output_hash,
                "exports": [],
                "completed_at": _now_iso(),
            }
        },
    )

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

    await db.users.update_one(
        {"id": user_id}, {"$inc": {"usage_this_month.analyses": 1}}
    )

    # ── Best-effort exports — generated AFTER completion, off the event loop ──
    # A failure or slowness here can never block completion or lose the report. The
    # blocking renderers run in a worker thread so the single web process keeps serving
    # requests (status polling, etc.) while PDFs/Word/Excel are built.
    try:
        proj = (
            await db.projects.find_one({"id": analysis.get("project_id")})
            if analysis.get("project_id")
            else None
        )
        export_meta = {
            "id": analysis_id,
            "mode_label": meta["label"],
            "project_name": proj["name"] if proj else "Quick Analysis",
            "completed_at": _now_iso(),
            "model_used": model_used,
            "blockchain_hash": output_hash,
        }
        exports = await asyncio.to_thread(generate_all_exports, output, export_meta)
        await db.analyses.update_one(
            {"id": analysis_id},
            {
                "$set": {
                    "exports": [
                        {
                            "format": e["format"],
                            "url": f"/api/analyses/{analysis_id}/export/{e['format']}",
                            "generated_at": _now_iso(),
                        }
                        for e in exports
                        if e.get("path")
                    ],
                }
            },
        )
    except Exception:  # noqa: BLE001 — exports are non-critical; the report is already saved
        logger.exception("export_generation_failed analysis=%s", analysis_id)


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
    limit: int = 50,
    user=Depends(get_current_user),
):
    db = get_db()
    q: dict = {}
    if project_id:
        q["project_id"] = project_id
    if user["role"] != "super_admin":
        q["requested_by"] = user["id"]
    items = await db.analyses.find(q, {"_id": 0, "output_markdown": 0}).sort("created_at", -1).to_list(limit)
    return {"items": items, "total": len(items)}


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
        {"_id": 0, "status": 1, "stage": 1, "processing_time_seconds": 1, "model_used": 1, "error_message": 1},
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
    await db.analyses.update_one(
        {"id": aid},
        {
            "$set": {
                "status": "queued",
                "stage": "queued",
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
        raise HTTPException(status_code=403, detail="Access denied")
    if user["role"] != "super_admin":
        check_feature(perms, "canDownloadReports")
    fmt_alias = {"docx": "word", "xlsx": "excel"}.get(fmt, fmt)
    check_export_access(perms, fmt_alias)
    ext_map = {"pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "csv": "csv", "markdown": "md"}
    ext = ext_map.get(fmt)
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported format")
    path = EXPORT_DIR / f"{aid}.{ext}"

    # Generate on demand if the file is not on disk yet. Exports are produced as a
    # best-effort step AFTER the report is saved, so a download can race ahead of them
    # (or a prior generation may have failed). Regenerate from the stored report here so
    # the user is never blocked by a missing export file.
    if not path.exists():
        if a.get("status") == "complete" and a.get("output_markdown"):
            proj = (
                await db.projects.find_one({"id": a.get("project_id")})
                if a.get("project_id")
                else None
            )
            export_meta = {
                "id": aid,
                "mode_label": a.get("mode_label", "Report"),
                "project_name": proj["name"] if proj else a.get("project_name", "Quick Analysis"),
                "completed_at": a.get("completed_at", ""),
                "model_used": a.get("model_used", ""),
                "blockchain_hash": a.get("blockchain_hash", ""),
            }
            try:
                await asyncio.to_thread(generate_all_exports, a["output_markdown"], export_meta)
            except Exception:  # noqa: BLE001
                logger.exception("on_demand_export_failed analysis=%s fmt=%s", aid, fmt)
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail="Export could not be generated. The report may still be processing.",
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
    if user["role"] != "super_admin" and a["requested_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    await db.analyses.delete_one({"id": aid})
    await audit_log(user["id"], "analysis.delete", "analysis", aid, request)
    return {"message": "Analysis deleted"}
