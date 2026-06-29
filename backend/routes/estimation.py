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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from config import settings
from db import get_db
from estimation.ai_extract import extract_quantities
from estimation.engine import CALCULATORS, apply_band_to_extracted, calculate
from estimation.pdf import render_pdf
from estimation.rates import supported_countries
from estimation.tonnage import get_or_lock_tonnage
from middleware.permission_guard import (
    audit_log,
    check_country,
    check_feature,
    load_permissions,
)
from security import block_write_if_readonly, get_current_user, sha256_hex

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

    file_pairs: list[tuple[str, str]] = []
    for f in file_docs:
        path = Path(settings.upload_dir) / f["storage_key"]
        if path.exists() and f.get("mime_type") in {
            "application/pdf", "text/plain", "text/csv",
            "image/png", "image/jpeg", "image/webp",
        }:
            file_pairs.append((str(path), f["mime_type"]))

    if not file_pairs:
        raise HTTPException(
            status_code=415,
            detail="Uploaded files are not in a supported format for AI analysis. Supported: PDF, PNG, JPG, WEBP, TXT, CSV.",
        )

    # Run extraction. Fabricator tonnage is shared via the project tonnage lock so the
    # estimate matches the tonnage reported by Master Intake / MTO Engine for the same files.
    now = datetime.now(timezone.utc).isoformat()
    session_id = sha256_hex(f"est-ai:{user['id']}:{role}:{now}")[:24]
    try:
        if role == "fabricator":
            lock = await get_or_lock_tonnage(
                [f["id"] for f in file_docs], file_pairs, session_id
            )
            if not lock:
                raise HTTPException(
                    status_code=422,
                    detail="Could not extract a usable tonnage from the uploaded drawings.",
                )
            extracted, engine = lock["extracted"], lock["engine"]
        else:
            extracted, engine = await extract_quantities(
                role=role, session_id=session_id, file_paths=file_pairs,
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}")

    # Apply rate band
    try:
        result = apply_band_to_extracted(
            role=role,
            extracted=extracted,
            rate_low=rate_low,
            rate_high=rate_high,
            country_code=country,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Auto-derive a project name from the first file if user didn't provide one
    if not project_name and file_docs:
        first = file_docs[0].get("original_name") or "Estimate"
        project_name = f"Estimate · {first[:60]}"

    record = {
        "id": sha256_hex(f"est-ai:{user['id']}:{session_id}")[:24],
        "user_id": user["id"],
        "role": role,
        "country": country,
        "project_name": project_name,
        "file_ids": [f["id"] for f in file_docs],
        "inputs": {
            "rate_low":  rate_low,
            "rate_high": rate_high,
            "extracted": extracted,
        },
        "result": result,
        "engine": engine,
        "created_at": now,
    }
    await db.estimates.insert_one(record)
    await audit_log(
        user["id"], "estimation.ai_calculate", "estimate", record["id"], request,
        extra={"role": role, "country": country, "files": len(file_pairs)},
    )
    record.pop("_id", None)
    return record


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
    items = await db.estimates.find(q, {"_id": 0}).sort("created_at", -1).to_list(100)
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
