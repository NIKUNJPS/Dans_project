"""AI take-off for the estimate page (/api/estimation/ai-calculate).

Runs the same binder pipeline as the analysis modes — page batching, the deterministic
take-off for fabricators, the piece-count engine for detailers — and adapts the result
to the shape `estimation.engine.apply_band_to_extracted` prices.
"""
from __future__ import annotations

import logging

from binder import plan_binder
from estimation.takeoff import SHORT_TON_KG
from estimation.tonnage import TakeoffCache
from pipeline import run_detailing_estimate, run_takeoff

logger = logging.getLogger(__name__)

_COUNTRY_TO_JURISDICTION = {
    "USA": "USA", "US": "USA",
    "CAN": "CANADA", "CANADA": "CANADA",
    "AUS": "AUSTRALIA", "AU": "AUSTRALIA", "AUSTRALIA": "AUSTRALIA",
}


def country_to_jurisdiction(country_code: str) -> str:
    return _COUNTRY_TO_JURISDICTION.get((country_code or "USA").upper(), "USA")


def takeoff_to_extracted(t: dict) -> dict:
    """Adapt a consolidated take-off to the estimate page's `extracted` payload."""
    tot = t["totals"]
    return {
        "tonnage": tot["gross_t"],
        "tonnage_t": tot["gross_t"],
        "tonnage_short": tot["gross_short_tons"],
        "member_tonnage": tot["net_t"],
        "net_kg": tot["net_kg"],
        "gross_kg": tot["gross_kg"],
        "allowances": tot["allowances"],
        "accessory_allowance_pct": round(sum(a["pct"] for a in tot["allowances"]), 2),
        "members_counted": tot["pieces"],
        "line_item_count": tot["line_items"],
        "primary_material": ", ".join(
            f"{g.get('normalized') or g.get('callout')}" for g in (t.get("grades") or [])[:3]
        ),
        "jurisdiction": t["jurisdiction"],
        "drawings_seen": len(t.get("sheets") or []),
        "line_items": [
            {k: r.get(k) for k in ("no", "type", "role", "mark", "profile", "qty", "raw_length", "length_mm",
                                   "unit_wt_kgm", "weight_kg", "weight_lb", "grade", "finish", "sheet", "method", "flag")}
            | {"source_sheet": r.get("sheet"), "surface": r.get("finish")}
            for r in t["rows"]
        ],
        "category_summary": [
            {"category": c["category"], "count": c["pieces"], "line_items": c["line_items"],
             "length_m": c["length_m"], "weight_kg": c["weight_kg"], "weight_lb": c["weight_lb"],
             "tons": c["tonnes"], "short_tons": round(c["weight_kg"] / SHORT_TON_KG, 3)}
            for c in t["categories"]
        ],
        "reconciliation": t["stats"],
        "assumptions": t.get("assumptions") or [],
        "excluded_scope": t.get("excluded_scope") or [],
        "open_rfis": [{"id": r["id"], "priority": r["priority"], "question": r["question"]} for r in t["rfis"]],
        "confidence": t.get("confidence", 0),
        "notes": t.get("notes", ""),
        "binder": t.get("binder"),
    }


def detailing_to_extracted(e: dict) -> dict:
    mid_hours = round((e["lock_hrs_low"] + e["lock_hrs_high"]) / 2.0, 1)
    return {
        "total_hours": mid_hours,
        "hours_low": e["lock_hrs_low"],
        "hours_high": e["lock_hrs_high"],
        "drawings": len(e.get("sheets") or []),
        "connections": sum(r["qty"] for r in e["rows"] if r["item"] in ("MISC_CLIP", "CONNECTION_PACKAGE")),
        "complexity": e["completeness"],
        "drawings_seen": len(e.get("sheets") or []),
        "line_items": [
            {"no": i, "task": f"{r['label']}" + (f" — {r['sub_type']}" if r["sub_type"] else ""), "qty": r["qty"],
             "unit": "ea", "hours_per_unit": r["adj"], "hours": round((r["low"] + r["high"]) / 2, 2),
             "basis": ", ".join(r["factors"]) or "standard", "flag": ""}
            for i, r in enumerate(e["rows"], 1)
        ],
        "assumptions": e.get("assumptions") or [],
        "open_rfis": [{"id": r["id"], "priority": r["priority"], "question": r["question"]} for r in e["rfis"]],
        "confidence": {"Complete": 85, "Partial": 68, "Limited": 45}.get(e["completeness"], 60),
        "notes": f"Buffer {e['buffer_pct']}% ({e['completeness']} information) · {e.get('notes') or ''}".strip(" ·"),
    }


async def extract_quantities(
    *,
    role: str,
    session_id: str,
    files: list[tuple[str, str, str]],
    file_ids: list[str] | None = None,
    country_code: str = "USA",
    progress=None,
) -> tuple[dict, str]:
    """Run the take-off (fabricator) or piece-count (detailer) and return
    (extracted_payload, engine_label)."""
    if not files:
        raise ValueError("Upload at least one drawing to run AI estimation.")
    jurisdiction = country_to_jurisdiction(country_code)
    if role == "fabricator":
        cache = TakeoffCache(file_ids or [])
        takeoff = await cache.get()
        if takeoff is None:
            plan = plan_binder(files)
            try:
                kwargs = {"progress": progress} if progress else {}
                takeoff, _engine, _usage = await run_takeoff(
                    plan, jurisdiction_hint=jurisdiction, label=session_id, **kwargs)
            finally:
                plan.cleanup()
            await cache.put(takeoff)
        return takeoff_to_extracted(takeoff), takeoff.get("engine", "STRUCTMIND CORE")

    plan = plan_binder(files)
    try:
        kwargs = {"progress": progress} if progress else {}
        est, engine, _usage = await run_detailing_estimate(
            plan, region_hint=jurisdiction, label=session_id, **kwargs)
    finally:
        plan.cleanup()
    return detailing_to_extracted(est), engine
