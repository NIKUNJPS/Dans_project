"""Estimation API — Detailer + Fabricator only.

Two flows are supported:
  • POST /api/estimation/ai-calculate  → drawing-driven (PRIMARY, surfaced in UI)
                                          Inputs: file_ids + rate_low + rate_high (+ optional project / country)
                                          AI extracts tonnage (fabricator) or drawings+complexity (detailer).
  • POST /api/estimation/calculate     → deterministic manual-input flow (kept for API / power users)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse

import logging
import shutil
import tempfile

from config import settings
from db import get_db
from estimation.ai_extract import extract_quantities
from estimation.engine import CALCULATORS, apply_band_to_extracted, calculate
from estimation.pdf import render_pdf
from estimation.rates import supported_countries
from file_prep import prepare_file
from middleware.permission_guard import (
    audit_log,
    check_country,
    check_feature,
    load_permissions,
)
from security import block_write_if_readonly, get_current_user, sha256_hex

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/estimation", tags=["estimation"])


# ─── COUNTRIES ──────────────────────────────────────────────────────────
@router.get("/countries")
async def list_countries(user=Depends(get_current_user), perms: dict = Depends(load_permissions)):
    """Return only the countries this user is allowed to estimate against."""
    all_countries = supported_countries()
    if user["role"] == "super_admin":
        return {"countries": all_countries}
    allowed = set(perms.get("estimationCountries", []))
    return {"countries": [c for c in all_countries if c["code"] in allowed]}


# ─── SIMPLIFIED SCHEMA (AI-driven UI) ───────────────────────────────────
@router.get("/schema/{role}")
async def schema_for_role(role: str, user=Depends(get_current_user)):
    role = role.lower()
    if role not in ("detailer", "fabricator"):
        raise HTTPException(status_code=400, detail=f"Estimation only supports detailer or fabricator (got '{role}').")
    if user["role"] != "super_admin" and role != user["role"]:
        raise HTTPException(status_code=403, detail="You may only request your own role's schema.")

    if role == "fabricator":
        return {
            "role": role,
            "schema": {
                "title": "Fabrication estimate",
                "subtitle": "Upload your drawings — STRUCTMIND CORE extracts the tonnage and applies your per-ton rate band.",
                "rate_unit": "/ ton",
                "rate_label_low":  "Your per-ton cost — LOW",
                "rate_label_high": "Your per-ton cost — HIGH",
                "rate_help_low":   "Lower bound of your shop's per-ton cost band (local currency).",
                "rate_help_high":  "Upper bound of your shop's per-ton cost band (local currency).",
                "default_low":  2400,
                "default_high": 3600,
            },
        }
    return {
        "role": role,
        "schema": {
            "title": "Detailing estimate",
            "subtitle": "Upload your drawings — the engine estimates the detailing hours and applies your hourly rate band.",
            "rate_unit": "/ hr",
            "rate_label_low":  "Detailer rate — LOW ($/hr)",
            "rate_label_high": "Detailer rate — HIGH ($/hr)",
            "rate_help_low":   "Lower bound of the detailer hourly rate.",
            "rate_help_high":  "Upper bound of the detailer hourly rate.",
            "default_low":  18,
            "default_high": 25,
        },
    }


# ─── AI-DRIVEN CALCULATE (PRIMARY) ──────────────────────────────────────
@router.post("/ai-calculate")
async def ai_calculate(
    payload: dict,
    request: Request,
    background: BackgroundTasks,
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    block_write_if_readonly(user)
    db = get_db()

    role = (payload.get("role") or user["role"]).lower()
    if role not in ("detailer", "fabricator"):
        raise HTTPException(status_code=400, detail="Estimation supports only 'detailer' or 'fabricator'.")
    if user["role"] != "super_admin" and role != user["role"]:
        raise HTTPException(status_code=403, detail="You may only run your own role's estimate.")

    try:
        rate_low  = float(payload.get("rate_low", 0))
        rate_high = float(payload.get("rate_high", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="rate_low and rate_high must be numbers.")
    # Detailer estimates are hours-based and default to the $18–25/hr band when no rate is supplied.
    if role == "detailer" and (rate_low <= 0 or rate_high <= 0):
        rate_low, rate_high = 18.0, 25.0
    if rate_low <= 0 or rate_high <= 0:
        raise HTTPException(status_code=422, detail="Both LOW and HIGH rates must be greater than 0.")

    file_ids: list[str] = payload.get("file_ids") or []
    if not file_ids:
        raise HTTPException(status_code=422, detail="Upload at least one drawing before calculating.")

    country = payload.get("country") or (user.get("country") or "USA")
    project_name = payload.get("project_name") or ""

    if user["role"] != "super_admin":
        if not perms.get("canRunEstimation", False):
            raise HTTPException(status_code=403, detail="Estimation is disabled for your account.")
        check_country(perms, country)
        max_files = perms.get("maxFilesPerAnalysis", 40)
        if len(file_ids) > max_files:
            raise HTTPException(status_code=413, detail=f"Too many files. Cap is {max_files}.")

    # Load files belonging to the user (or any file for super_admin) and prep for LLM
    file_query = {"id": {"$in": file_ids}}
    if user["role"] != "super_admin":
        file_query["uploaded_by"] = user["id"]
    file_docs = await db.files.find(file_query).to_list(1000)
    if not file_docs:
        raise HTTPException(status_code=404, detail="None of the uploaded files were found.")

    # Keep the user's upload order so the binder reads in sequence.
    order = {fid: i for i, fid in enumerate(file_ids)}
    file_docs.sort(key=lambda f: order.get(f["id"], 1_000_000))

    scratch = Path(tempfile.mkdtemp(prefix="est_prep_"))
    files: list[tuple[str, str, str]] = []
    for f in file_docs:
        pair, _status = prepare_file(f, Path(settings.upload_dir), scratch)
        if pair:
            files.append((pair[0], pair[1], f.get("original_name") or f["storage_key"]))

    if not files:
        shutil.rmtree(scratch, ignore_errors=True)
        raise HTTPException(
            status_code=415,
            detail="Uploaded files are not in a supported format for AI analysis. Supported: PDF, images, "
                   "Excel, Word, CSV/TXT and text-based fabrication files (export CAD/BIM to PDF).",
        )

    if not project_name and file_docs:
        first = file_docs[0].get("original_name") or "Estimate"
        project_name = f"Estimate · {first[:60]}"

    # The take-off for a full binder takes minutes, so it runs in the background and the
    # client polls GET /api/estimation/{id} until status is "complete" (or "failed").
    now = datetime.now(timezone.utc).isoformat()
    session_id = sha256_hex(f"est-ai:{user['id']}:{role}:{now}")[:24]
    record = {
        "id": sha256_hex(f"est-ai:{user['id']}:{session_id}")[:24],
        "user_id": user["id"],
        "role": role,
        "country": country,
        "project_name": project_name,
        "project_id": payload.get("project_id") or None,
        "file_ids": [f["id"] for f in file_docs],
        "inputs": {"rate_low": rate_low, "rate_high": rate_high},
        "status": "processing",
        "result": None,
        "engine": "",
        "error_message": "",
        "created_at": now,
    }
    await db.estimates.insert_one(record)
    await audit_log(
        user["id"], "estimation.ai_calculate", "estimate", record["id"], request,
        extra={"role": role, "country": country, "files": len(files)},
    )
    background.add_task(
        _run_ai_estimate, record["id"], role, session_id, files, record["file_ids"],
        country, rate_low, rate_high, str(scratch),
    )
    record.pop("_id", None)
    return record


async def _run_ai_estimate(eid: str, role: str, session_id: str, files: list, file_ids: list[str],
                           country: str, rate_low: float, rate_high: float, scratch: str) -> None:
    db = get_db()

    async def progress(label: str, done: int, total: int) -> None:
        await db.estimates.update_one({"id": eid}, {"$set": {"progress": {"label": label, "done": done, "total": total}}})

    try:
        extracted, engine = await extract_quantities(
            role=role, session_id=session_id, files=files, file_ids=file_ids, country_code=country,
            progress=progress,
        )
        result = apply_band_to_extracted(
            role=role, extracted=extracted, rate_low=rate_low, rate_high=rate_high, country_code=country,
        )
        await db.estimates.update_one({"id": eid}, {"$set": {
            "status": "complete", "result": result, "engine": engine,
            "inputs.extracted": extracted,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }})
    except Exception as e:  # noqa: BLE001
        logger.exception("ai_estimate_failed id=%s", eid)
        await db.estimates.update_one({"id": eid}, {"$set": {
            "status": "failed", "error_message": f"Extraction failed: {e}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ─── DETERMINISTIC CALCULATE (KEPT FOR API / POWER USERS) ────────────────
@router.post("/calculate")
async def post_calculate(
    payload: dict,
    request: Request,
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    block_write_if_readonly(user)
    role     = (payload.get("role") or user["role"]).lower()
    country  = payload.get("country") or "USA"
    inputs   = payload.get("inputs") or {}
    project  = payload.get("project_name") or "Quick Estimate"

    if role not in CALCULATORS or role == "super_admin":
        if role not in ("detailer", "fabricator"):
            raise HTTPException(status_code=400, detail="Estimation supports only 'detailer' or 'fabricator'.")

    if user["role"] != "super_admin" and role != user["role"]:
        raise HTTPException(status_code=403, detail="You may only run your own role's estimate.")

    if user["role"] != "super_admin":
        check_feature(perms, "canRunEstimation")
        check_country(perms, country)

    if role == "fabricator":
        try:
            low  = float(inputs.get("cost_per_ton_low", 0))
            high = float(inputs.get("cost_per_ton_high", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="cost_per_ton_low and cost_per_ton_high must be numbers.")
        if low <= 0 or high <= 0:
            raise HTTPException(status_code=422, detail="Fabricator estimates require cost_per_ton_low and cost_per_ton_high (both > 0).")

    try:
        result = calculate(role, inputs, country)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "id": sha256_hex(f"est:{user['id']}:{role}:{now}")[:24],
        "user_id": user["id"],
        "role": role,
        "country": country,
        "project_name": project,
        "inputs": payload.get("inputs") or {},
        "result": result,
        "created_at": now,
    }
    await db.estimates.insert_one(record)
    await audit_log(user["id"], "estimation.calculate", "estimate", record["id"], request,
                    extra={"role": role, "country": country})
    record.pop("_id", None)
    return record


@router.get("/")
async def list_estimates(user=Depends(get_current_user)):
    db = get_db()
    q = {} if user["role"] == "super_admin" else {"user_id": user["id"]}
    projection = {"_id": 0, "inputs.extracted": 0, "result.visible.line_items": 0, "result.meta": 0}
    items = await db.estimates.find(q, projection).sort("created_at", -1).to_list(100)
    return {"items": items}


@router.get("/{eid}")
async def get_estimate(eid: str, user=Depends(get_current_user)):
    db = get_db()
    e = await db.estimates.find_one({"id": eid}, {"_id": 0})
    if not e:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if user["role"] != "super_admin" and e["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return e


@router.get("/{eid}/pdf")
async def download_pdf(
    eid: str,
    request: Request,
    user=Depends(get_current_user),
    perms: dict = Depends(load_permissions),
):
    db = get_db()
    e = await db.estimates.find_one({"id": eid})
    if not e:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if user["role"] != "super_admin" and e["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    if user["role"] != "super_admin":
        check_feature(perms, "canDownloadReports")

    if e.get("status") == "processing":
        raise HTTPException(status_code=409, detail="The estimate is still being calculated.")
    if e.get("status") == "failed":
        raise HTTPException(status_code=409, detail=e.get("error_message") or "The estimate failed.")

    # AI-driven records already store the full result; deterministic ones recompute.
    full_result = e.get("result")
    if not full_result or "visible" not in full_result:
        try:
            full_result = calculate(e["role"], e["inputs"], e["country"])
        except ValueError as ex:
            raise HTTPException(status_code=422, detail=str(ex))

    path = render_pdf(full_result, e.get("project_name", "Project"), filename=f"{eid}.pdf")
    if not Path(path).exists():
        raise HTTPException(status_code=500, detail="PDF render failed")
    await audit_log(user["id"], "estimation.pdf", "estimate", eid, request)
    return FileResponse(
        path,
        filename=f"STRUCTMIND_{e['role'].title()}_Estimate.pdf",
        media_type="application/pdf",
    )


@router.delete("/{eid}")
async def delete_estimate(eid: str, request: Request, user=Depends(get_current_user)):
    block_write_if_readonly(user)
    db = get_db()
    e = await db.estimates.find_one({"id": eid})
    if not e:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if user["role"] != "super_admin" and e["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    await db.estimates.delete_one({"id": eid})
    await audit_log(user["id"], "estimation.delete", "estimate", eid, request)
    return {"message": "Estimate deleted"}
