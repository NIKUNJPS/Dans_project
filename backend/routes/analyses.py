"""Analysis routes: queue, status, list, export, rerun, result, quality gate, release.

Master MTO and Estimation reports pass the quality gate (quality_gate.py) before release:
a report whose gate fails is kept for review in the app, but its downloads are held
(409) until every check passes on a re-run or the report owner releases it with a
written reason (audited). Their files are named 4xtrsuct_Master_MTO_<Project> and
4xtrsuct_Estimation_<Project>.

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
from starlette.background import BackgroundTask

from config import settings
from db import get_db
from export_service import (
    EXPORT_DIR,
    EXTENSIONS,
    download_name,
    generate_all_exports,
    generate_export,
    remove_exports,
)
from file_prep import prepare_file
from claude_engine import CapacityUnavailable, engine_label, has_credentials
from estimation.tonnage import TakeoffCache
from pipeline import DET_ESTIMATE_MODES, FAB_ESTIMATE_MODES, TAKEOFF_MODES, Cancelled, run_mode
try:
    import quality_gate
except ImportError:  # pragma: no cover — module lands with the quality-gate build
    quality_gate = None
from middleware.permission_guard import (
    audit_log,
    bump_usage,
    check_mode_access,
    check_usage_limit,
    load_permissions,
)
from models import AnalysisCreate, AnalysisRelease, AnalysisRename
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

# Per-analysis pre-read artefacts (sheet_register.json, callouts.json, metals_sweep.json …).
WORK_DIR = Path(settings.upload_dir) / "work"
GATED_MODES = TAKEOFF_MODES | DET_ESTIMATE_MODES | FAB_ESTIMATE_MODES
_requeue_tasks: set[asyncio.Task] = set()


def _work_dir(aid: str) -> Path:
    return WORK_DIR / re.sub(r"[^A-Za-z0-9_-]", "", aid)


def _remove_work(aid: str) -> None:
    shutil.rmtree(_work_dir(aid), ignore_errors=True)


def doc_kind(mode_id: str) -> str:
    if mode_id in TAKEOFF_MODES:
        return "mto"
    if mode_id in DET_ESTIMATE_MODES or mode_id in FAB_ESTIMATE_MODES:
        return "estimate"
    return "report"


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

        work = _work_dir(analysis_id)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
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
            work_dir=str(work),
        )
        output, model_used = result.markdown, result.engine
    except Cancelled:
        logger.info("analysis_cancelled id=%s (deleted while running)", analysis_id)
        _remove_work(analysis_id)
        return
    except CapacityUnavailable as e:
        await _requeue_for_capacity(db, analysis_id, analysis, str(e))
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
        # Tiles are only needed while the model reads them; the JSON artefacts stay.
        shutil.rmtree(_work_dir(analysis_id) / "tiles", ignore_errors=True)

    elapsed = round(time.time() - started, 2)
    output_hash = sha256_hex(output)

    # Issue counts from the report's own priority labels.
    up = output.upper()
    crit = len(re.findall(r"\bCRITICAL\b", up))
    major = len(re.findall(r"\b(?:MAJOR|URGENT)\b", up))
    minor = len(re.findall(r"\b(?:MINOR|STANDARD|OBSERVATION)\b", up))
    issues = {"critical": crit, "major": major, "minor": minor, "total": crit + major + minor}
    quality = max(0, min(100, 100 - crit * 8 - major * 3 - minor * 1))

    # Quality gate (Master MTO / Estimation): computed from the same take-off that produced
    # the report; the document-render check (8) is added once the exports exist.
    gate = None
    if quality_gate is not None and mode_id in GATED_MODES and result.takeoff is not None:
        try:
            if mode_id in DET_ESTIMATE_MODES and result.estimate is not None:
                gate = quality_gate.gate_estimate(result.estimate, output, result.takeoff)
            else:
                gate = quality_gate.gate_mto(result.takeoff, output)
        except Exception:  # noqa: BLE001 — a gate crash holds the report rather than releasing it
            logger.exception("quality_gate_failed analysis=%s", analysis_id)
            gate = {"version": 0, "kind": doc_kind(mode_id), "passed": False, "failed": [0], "checks": [
                {"id": 0, "key": "gate", "name": "Quality gate", "passed": False,
                 "detail": "The quality gate could not be evaluated; re-run the report.", "items": []}]}
    project_info = _project_info(result.takeoff) if result.takeoff is not None else None

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
                "model_id": result.model_id,
                "processing_time_seconds": elapsed,
                "issues_found": issues,
                "quality_score": quality,
                "blockchain_hash": output_hash,
                "binder": result.meta.get("binder"),
                "usage": result.meta.get("usage"),
                "takeoff_summary": result.meta.get("takeoff_summary"),
                "estimate_summary": result.meta.get("estimate"),
                "ingest_summary": result.meta.get("ingest_summary"),
                "project_info": project_info,
                "quality_gate": gate,
                "release_state": None if gate is None else ("released" if gate.get("passed") else "blocked"),
                "release": None,
                "capacity_requeues": 0,
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
        # Re-read: the report (or its project) may have been renamed while it was running.
        latest = await db.analyses.find_one({"id": analysis_id}, {"_id": 0, "output_markdown": 0}) or analysis
        export_meta = await _export_meta(db, {**latest, "mode_label": meta["label"]})
        exports = await asyncio.to_thread(generate_all_exports, output, export_meta)
        update: dict = {}
        if gate is not None:
            paths = {e["format"]: e.get("path") for e in exports}
            check = await asyncio.to_thread(quality_gate.check_exports, paths.get("docx"), paths.get("pdf"))
            gate = quality_gate.with_export_check(gate, check)
            update["quality_gate"] = gate
            update["release_state"] = "released" if gate.get("passed") else "blocked"
            logger.info("quality_gate analysis=%s %s", analysis_id, quality_gate.summarize(gate))
        if await _renamed_since(db, analysis_id, export_meta):
            _remove_exports(analysis_id)  # rebuilt with the new name on the next download
            exports = []
        update["exports"] = [
            {"format": e["format"], "url": f"/api/analyses/{analysis_id}/export/{e['format']}",
             "generated_at": _now_iso()}
            for e in exports if e.get("path")
        ]
        await db.analyses.update_one({"id": analysis_id}, {"$set": update})
    except Exception:  # noqa: BLE001 — exports are non-critical; the report is already saved
        logger.exception("export_generation_failed analysis=%s", analysis_id)


_DOC_TITLES = {
    "mto": ("MASTER MTO ENGINE", "Master MTO", "Master_MTO"),
    "estimate": ("ESTIMATION PRO — HOURS-BASED QUOTATION ENGINE", "Estimation", "Estimation"),
}


def _project_info(takeoff: dict) -> dict:
    proj = takeoff.get("project") or {}
    reg = takeoff.get("register") or []
    return {
        "name": proj.get("name", ""), "number": proj.get("number", ""),
        "address": proj.get("address") or proj.get("location", ""), "owner": proj.get("owner", ""),
        "architect": proj.get("architect", ""), "engineer": proj.get("engineer") or proj.get("eor", ""),
        "contractor": proj.get("contractor", ""), "drawing_set": proj.get("drawing_set") or proj.get("stage", ""),
        "sheet_count": len(reg) or len(takeoff.get("sheets") or []),
    }


def doc_project_name(a: dict) -> str:
    """The <Project_Name> in a deliverable's name and header: the name the user typed for
    the report, else the project name from the drawings' title block, else the project,
    else the report's default name (first drawing)."""
    if a.get("title") and a.get("title_source", "user") == "user":
        return a["title"]
    info = a.get("project_info") or {}
    if info.get("name"):
        return info["name"]
    if a.get("project_name") and a["project_name"] != "Quick Analysis":
        return a["project_name"]
    return a.get("title") or "Project"


async def _export_meta(db, a: dict) -> dict:
    proj = await db.projects.find_one({"id": a.get("project_id")}) if a.get("project_id") else None
    kind = doc_kind(a.get("mode", ""))
    project_name = proj["name"] if proj else (a.get("project_name") or "Quick Analysis")
    meta = {
        "id": a["id"],
        "mode_label": a.get("mode_label", "Report"),
        "title": a.get("title") or "",
        "project_name": project_name,
        "completed_at": a.get("completed_at", ""),
        "model_used": a.get("model_used", ""),
        "model_label": engine_label(a.get("model_id") or ""),
        "blockchain_hash": a.get("blockchain_hash", ""),
        "doc_kind": kind,
        "doc_title": _DOC_TITLES[kind][0] if kind in _DOC_TITLES else (a.get("mode_label") or "Report").upper(),
        "header_report": _DOC_TITLES[kind][1] if kind in _DOC_TITLES else (a.get("mode_label") or "Report"),
        "project_info": a.get("project_info") or {},
    }
    if kind in _DOC_TITLES:
        meta["project_name"] = doc_project_name({**a, "project_name": project_name})
    return meta


def export_filename(a: dict, ext: str, project_name: str | None = None) -> str:
    """4xtrsuct_Master_MTO_<Project>.docx / 4xtrsuct_Estimation_<Project>.docx; other
    reports keep '<report name>_<mode>.<ext>'."""
    kind = doc_kind(a.get("mode", ""))
    if kind in _DOC_TITLES:
        return f"{download_name('4xtrsuct', _DOC_TITLES[kind][2], project_name or doc_project_name(a))}.{ext}"
    return f"{download_name(report_name(a), a.get('mode_label', 'report'))}.{ext}"


async def _requeue_for_capacity(db, aid: str, analysis: dict, error: str) -> None:
    """The model stayed busy for the whole capacity wait: put the job back in the queue
    (same model, later) instead of failing it or running it on another model."""
    tries = int(analysis.get("capacity_requeues") or 0)
    if tries >= max(0, settings.anthropic_requeue_attempts):
        await db.analyses.update_one({"id": aid}, {"$set": {
            "status": "failed", "stage": "failed", "completed_at": _now_iso(),
            "error_message": ("STRUCTMIND CORE capacity stayed unavailable after "
                              f"{tries + 1} attempts. Please re-run the analysis later. ({error})")}})
        return
    delay = max(1, settings.anthropic_requeue_delay_minutes)
    label = f"Waiting for model capacity — queued, retry in {delay} min"
    await db.analyses.update_one({"id": aid}, {"$set": {
        "status": "queued", "stage": label, "capacity_requeues": tries + 1,
        "requeue_at": datetime.fromtimestamp(time.time() + delay * 60, timezone.utc).isoformat(),
        "progress": {"label": label, "done": 0, "total": 1}}})
    logger.warning("analysis_requeued_for_capacity id=%s attempt=%d delay_min=%d", aid, tries + 1, delay)
    _schedule_run(aid, delay * 60)


def _schedule_run(aid: str, delay_s: float) -> None:
    async def later():
        await asyncio.sleep(max(0.0, delay_s))
        a = await get_db().analyses.find_one({"id": aid}, {"_id": 0, "status": 1})
        if a and a.get("status") == "queued":
            await _run_analysis_task(aid)

    task = asyncio.create_task(later())
    _requeue_tasks.add(task)
    task.add_done_callback(_requeue_tasks.discard)


async def resume_capacity_queue(db) -> int:
    """At startup: restart jobs that were waiting in the capacity queue."""
    n = 0
    async for a in db.analyses.find({"status": "queued", "requeue_at": {"$ne": None}},
                                    {"_id": 0, "id": 1, "requeue_at": 1}):
        try:
            due = datetime.fromisoformat(a["requeue_at"]).timestamp() - time.time()
        except (TypeError, ValueError):
            due = 0
        _schedule_run(a["id"], max(5.0, due))
        n += 1
    return n


def _remove_exports(aid: str) -> None:
    remove_exports(aid)


async def _renamed_since(db, aid: str, built_meta: dict) -> bool:
    """True when the report's name or project name changed after `built_meta` was read."""
    now = await db.analyses.find_one({"id": aid}, {"_id": 0, "output_markdown": 0})
    if not now:
        return False
    fresh = await _export_meta(db, now)
    return (fresh["title"], fresh["project_name"]) != (built_meta["title"], built_meta["project_name"])


async def backfill_report_titles(db) -> int:
    """Name Quick Analyses created before reports had names, after their first drawing.

    Runs at startup; idempotent (only touches quick reports with no name yet).
    """
    named = 0
    cursor = db.analyses.find(
        {"project_id": {"$in": [None, ""]}, "title": {"$in": [None, ""]}},
        {"_id": 0, "id": 1, "file_ids": 1},
    )
    async for a in cursor:
        if not a.get("file_ids"):
            continue
        f = await db.files.find_one({"id": a["file_ids"][0]}, {"_id": 0, "original_name": 1})
        title = Path(f["original_name"]).stem.strip()[:120] if f and f.get("original_name") else ""
        if title:
            await db.analyses.update_one({"id": a["id"]}, {"$set": {"title": title}})
            _remove_exports(a["id"])  # covers are rebuilt with the name on the next download
            named += 1
    return named


def report_name(a: dict) -> str:
    """What the user calls this report: its own name, else its project, else 'Quick Analysis'."""
    return a.get("title") or a.get("project_name") or "Quick Analysis"


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

    # A Quick Analysis the user didn't name is called after its first drawing.
    title = (data.title or "").strip()
    title_source = "user" if title else "default"
    if not title and not data.project_id and data.file_ids:
        fq = {"id": data.file_ids[0]}
        if user["role"] != "super_admin":
            fq["uploaded_by"] = user["id"]
        first = await db.files.find_one(fq, {"_id": 0, "original_name": 1})
        if first and first.get("original_name"):
            title = Path(first["original_name"]).stem.strip()[:120]

    full = await db.users.find_one({"id": user["id"]})
    now = _now_iso()
    aid = sha256_hex(f"analysis:{user['id']}:{data.mode}:{now}")[:24]
    doc = {
        "id": aid,
        "project_id": data.project_id,
        "project_name": project_name,
        "title": title,
        "title_source": title_source,
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
    for a in items:
        if doc_kind(a.get("mode", "")) in _DOC_TITLES:
            a["download_base"] = export_filename(a, "x")[:-2]
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
            _remove_work(aid)
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
    a["can_edit"] = await _can_delete(db, a, user)
    a["can_release"] = a["can_edit"] and a.get("release_state") == "blocked"
    a["doc_kind"] = doc_kind(a.get("mode", ""))
    if a["doc_kind"] in _DOC_TITLES:
        proj = await db.projects.find_one({"id": a.get("project_id")}, {"_id": 0, "name": 1}) if a.get("project_id") else None
        a["download_base"] = export_filename({**a, "project_name": (proj or {}).get("name") or a.get("project_name")},
                                             "x")[:-2]
    return a


@router.post("/{aid}/release")
async def release_analysis(aid: str, data: AnalysisRelease, request: Request, user=Depends(get_current_user)):
    """Release a report the quality gate held. Owner (or super admin) only; the reason and
    the failed checks are recorded on the report and in the audit log."""
    block_write_if_readonly(user)
    db = get_db()
    a = await db.analyses.find_one({"id": aid}, {"_id": 0, "output_markdown": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if not await _can_delete(db, a, user):
        raise HTTPException(status_code=403, detail="Only the report's owner can release it")
    if a.get("release_state") != "blocked":
        raise HTTPException(status_code=409, detail="This report is not held by the quality gate.")
    failed = [c["name"] for c in (a.get("quality_gate") or {}).get("checks") or [] if not c.get("passed")]
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0, "first_name": 1, "last_name": 1, "email": 1})
    release = {
        "by": user["id"],
        "by_name": f"{(full or {}).get('first_name', '')} {(full or {}).get('last_name', '')}".strip()
        or (full or {}).get("email", ""),
        "at": _now_iso(), "reason": data.reason, "failed_checks": failed,
    }
    await db.analyses.update_one({"id": aid}, {"$set": {"release_state": "released_with_exceptions",
                                                        "release": release}})
    await audit_log(user["id"], "analysis.release_with_exceptions", "analysis", aid, request,
                    extra={"reason": data.reason, "failed": failed})
    return {"id": aid, "release_state": "released_with_exceptions", "release": release}


@router.post("/{aid}/estimation")
async def create_estimation_from_mto(
    aid: str,
    background: BackgroundTasks,
    request: Request,
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    """Create the Estimation report for the same drawing set as a Master MTO report. It
    reuses the MTO's locked take-off, so both documents carry the same quantities."""
    db = get_db()
    a = await db.analyses.find_one({"id": aid}, {"_id": 0, "output_markdown": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if user["role"] != "super_admin" and a["requested_by"] != user["id"]:
        p = await db.projects.find_one({"id": a.get("project_id")}) if a.get("project_id") else None
        if not p or not _can_access_project(p, user):
            raise HTTPException(status_code=403, detail="Access denied")
    if a.get("mode") not in TAKEOFF_MODES or a.get("status") != "complete":
        raise HTTPException(status_code=409, detail="Create the estimation from a completed Master MTO report.")
    mode = "FABRICATOR_ESTIMATION_PRO" if user["role"] == "fabricator" else "ESTIMATION_PRO"
    data = AnalysisCreate(project_id=a.get("project_id"), file_ids=a.get("file_ids") or [], mode=mode,
                          input_text=a.get("input_text") or "",
                          title=(a.get("title") if a.get("title_source", "user") == "user" else None))
    doc = await create_analysis(data, background, request, user=user, perms=perms)
    if a.get("title") and a.get("title_source", "user") != "user":
        await db.analyses.update_one({"id": doc["id"]}, {"$set": {"title": a["title"], "title_source": "default"}})
        doc["title"] = a["title"]
    doc["source_analysis_id"] = aid
    await db.analyses.update_one({"id": doc["id"]}, {"$set": {"source_analysis_id": aid}})
    return doc


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
                "quality_gate": None,
                "release_state": None,
                "release": None,
                "capacity_requeues": 0,
                "requeue_at": None,
            }
        },
    )
    _remove_work(aid)
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
):
    """Download a report in any format. Every user who can open the report can download
    and print it; there is no per-format or per-user download permission."""
    db = get_db()
    a = await db.analyses.find_one({"id": aid})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if user["role"] != "super_admin" and a["requested_by"] != user["id"]:
        p = await db.projects.find_one({"id": a.get("project_id")}) if a.get("project_id") else None
        if not p or not _can_access_project(p, user):
            raise HTTPException(status_code=403, detail="Access denied")
    exporter = {"pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "csv": "csv", "markdown": "markdown"}.get(fmt)
    if not exporter:
        raise HTTPException(status_code=400, detail="Unsupported format")
    if a.get("release_state") == "blocked":
        gate = a.get("quality_gate") or {}
        failed = [c["name"] for c in gate.get("checks") or [] if not c.get("passed")]
        raise HTTPException(status_code=409, detail=(
            "This report is held by the quality gate (failed: " + (", ".join(failed) or "see report") + "). "
            "Review the checks on the report page; the report owner can release it with a reason."))
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
        # Renamed while building: serve this copy, then drop it so the next download is rebuilt.
        stale = await _renamed_since(db, aid, export_meta)
    else:
        stale = False
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "md": "text/markdown",
    }
    proj = await db.projects.find_one({"id": a.get("project_id")}, {"_id": 0, "name": 1}) if a.get("project_id") else None
    filename = export_filename({**a, "project_name": (proj or {}).get("name") or a.get("project_name")}, ext)
    await audit_log(user["id"], "analysis.export", "analysis", aid, request, extra={"format": fmt})
    return FileResponse(str(path), filename=filename, media_type=media.get(ext, "application/octet-stream"),
                        background=BackgroundTask(remove_exports, aid) if stale else None)


@router.patch("/{aid}")
async def rename_analysis(aid: str, data: AnalysisRename, request: Request, user=Depends(get_current_user)):
    """Rename a report. Its exports are rebuilt with the new name on the next download."""
    block_write_if_readonly(user)
    db = get_db()
    a = await db.analyses.find_one({"id": aid}, {"_id": 0, "id": 1, "requested_by": 1, "project_id": 1, "title": 1})
    if not a:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if not await _can_delete(db, a, user):
        raise HTTPException(status_code=403, detail="Only the report's owner can rename it")
    await db.analyses.update_one({"id": aid}, {"$set": {"title": data.title, "title_source": "user"}})
    _remove_exports(aid)
    await audit_log(user["id"], "analysis.rename", "analysis", aid, request,
                    extra={"from": a.get("title") or "", "to": data.title})
    return {"id": aid, "title": data.title}


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
    _remove_work(aid)
    await audit_log(user["id"], "analysis.delete", "analysis", aid, request)
    return {"message": "Analysis deleted"}
