"""Itemized material take-off engine — the accuracy core.

Instead of asking the model for a single bulk tonnage (the old, inaccurate path),
this runs a disciplined member-by-member take-off, then RE-COMPUTES every weight in
Python from the published unit-weight tables. The project tonnage is the exact sum of
that recomputed column — a deterministic calculation, so the same drawing set always
produces the same number, and any arithmetic the model got wrong is corrected and
flagged. This is what makes the estimate "accurate like a human".

Output contract from the model (robust to the streaming continuation stitcher):

    <<<META>>>
    { ...small JSON: jurisdiction, unit_system, primary_material, drawings_seen,
      confidence, assumptions[], open_rfis[], notes ... }
    <<<END_META>>>
    <<<ROWS>>>
    no|type|mark|profile|qty|length_mm|unit_weight_kgm|weight_kg|grade|surface|source_sheet|method|flag
    1|W-SHAPE|B1|W12x26|4|6096|38.7|944.0|A992|Primer|S-101|BOM DIRECT|
    ...
    <<<END_ROWS>>>

META is small and emitted first, so it never truncates. ROWS are one pipe-delimited
line per piece — line-oriented output survives the continuation stitcher (a row cut at
the token limit is resumed mid-row), and each *complete* line is parsed independently.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from estimation import weights as W
from gemini_service import (
    MODEL_CHAIN,
    FILES_BETA,  # noqa: F401  (kept for parity/import-time validation)
    engine_label,
    _get_client,
    _upload_files_sync,
    _cleanup_files,
    _file_block,
    _generate_with_continuation,
)

logger = logging.getLogger(__name__)

# Fabricator take-off columns (order matters — this is the contract in the prompt).
FAB_COLUMNS = [
    "no", "type", "mark", "profile", "qty", "length_mm", "unit_weight_kgm",
    "weight_kg", "grade", "surface", "source_sheet", "method", "flag",
]
# Detailer workload columns.
DET_COLUMNS = [
    "no", "task", "qty", "unit", "hours_per_unit", "hours", "basis", "flag",
]

ACCESSORY_ALLOWANCE_PCT = 3.0  # bolts, welds, connection plates, misc hardware
RECONCILE_TOLERANCE_PCT = 3.0  # model weight vs Python-recomputed weight

# Detailer productivity fallback (used only if the model returns no hour rows).
_DET_HRS_PER_DWG = {
    "Low": 1.5, "Medium": 2.5, "High": 4.0, "AESS": 6.5, "Critical": 8.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

def _fab_prompt(jurisdiction: str) -> str:
    return f"""You are STRUCTMIND CORE — a principal structural-steel estimator (25+ years)
producing a fabrication-grade material take-off. Work like a human quantity surveyor:
read EVERY sheet, schedule, BOM and detail end to end before writing a single row.

{W.weight_table_text(jurisdiction)}

METHOD (perform silently, then output only the two blocks below):
  1. Detect jurisdiction (USA / CANADA / AUSTRALIA) once and apply that catalogue
     throughout. Prefer the BOM / member schedule for marks, lengths, grades; use
     framing plans + grid dimensions where a schedule is absent.
  2. ONE ROW PER PIECE. Every distinct mark/profile/length is its own row — never
     aggregate, never write "typical" or "(x N)". If a mark repeats, set Qty.
  3. For each linear member give length_mm and the unit_weight_kgm from the table
     above, then weight_kg = Qty x (length_mm/1000) x unit_weight_kgm.
     For plates/bars: weight_kg = thk(mm) x width(mm) x length(m) x 0.00785, and put
     the plate's own kg/m-equivalent (weight_kg / (Qty x length_m)) in unit_weight_kgm,
     or 0 if you cannot; the platform re-computes and reconciles every weight anyway.
  4. Include secondary steel, bracing, miscellaneous, embeds, base plates, stiffeners,
     anchor bolts and structural bolts. Anything with no length/weight on the drawings
     gets a row with the missing cell left blank and a matching RFI in META.open_rfis.
  5. NEVER invent a value. A genuine gap is a blank cell + an RFI, never a guess.

OUTPUT — emit EXACTLY these two blocks, META first, nothing before or after:

<<<META>>>
{{"jurisdiction":"USA","unit_system":"imperial","primary_material":"A992 W-shapes",
"drawings_seen":0,"confidence":0,"assumptions":["..."],
"open_rfis":[{{"id":"RFI-MTO-001","priority":"CRITICAL","question":"...","blocked_fields":"..."}}],
"notes":"one-line take-off basis"}}
<<<END_META>>>
<<<ROWS>>>
{"|".join(FAB_COLUMNS)}
1|W-SHAPE|B1|W12x26|4|6096|38.7|944.0|A992|Primer|S-101|BOM DIRECT|
<<<END_ROWS>>>

Rules for the ROWS block:
  - First line is the header shown above, then one line per piece, pipe-delimited,
    in the SAME column order. Use a blank between pipes for a genuinely missing cell.
  - Numbers only in qty / length_mm / unit_weight_kgm / weight_kg (no units, no commas).
  - confidence is 0-100 (your honest completeness/reliability of the take-off).
  - Keep going until the final piece — the platform stitches long output, so never
    summarise, paginate, or stop early. Do not repeat the header."""


def _det_prompt(jurisdiction: str) -> str:
    return f"""You are STRUCTMIND CORE — a senior steel-detailing lead estimating the
detailing WORKLOAD in hours. Read every drawing to gauge scope, connection density and
complexity, then build the hours from a task-by-task breakdown like a real detailer.

OUTPUT — emit EXACTLY these two blocks, META first, nothing before or after:

<<<META>>>
{{"jurisdiction":"USA","unit_system":"imperial","drawings":0,"connections":0,
"complexity":"Medium","complexity_multiplier":1.0,"drawings_seen":0,"confidence":0,
"assumptions":["..."],
"open_rfis":[{{"id":"RFI-DET-001","priority":"STANDARD","question":"...","blocked_fields":"..."}}],
"notes":"one-line basis"}}
<<<END_META>>>
<<<ROWS>>>
{"|".join(DET_COLUMNS)}
1|Production/shop drawings|120|dwgs|2.5|300|Modelling + drawing time|
2|Connection detailing|450|conn|0.35|157.5|Bolted + welded mix|
3|Checking / QC|1|lot|60|60|10% of production|
4|Revision allowance|1|cycle|45|45|One review cycle|
<<<END_ROWS>>>

Rules:
  - First line is the header shown above, then one task per line, pipe-delimited.
  - hours = qty x hours_per_unit (numbers only, no units).
  - complexity is one of Low / Medium / High / AESS / Critical.
  - confidence is 0-100. Never invent scope; unknowns become an RFI in META."""


# ─────────────────────────────────────────────────────────────────────────────
# PARSING
# ─────────────────────────────────────────────────────────────────────────────

_META_RE = re.compile(r"<<<META>>>(.*?)<<<END_META>>>", re.DOTALL | re.IGNORECASE)
_ROWS_RE = re.compile(r"<<<ROWS>>>(.*?)(?:<<<END_ROWS>>>|$)", re.DOTALL | re.IGNORECASE)


def _num(value, default: float = 0.0) -> float:
    if value is None:
        return default
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "—", "NF", "N/A", "TBD"):
        return default
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else default


def parse_meta(text: str) -> dict:
    """Extract and JSON-parse the META block; tolerant of stray text/fences."""
    m = _META_RE.search(text or "")
    blob = m.group(1) if m else ""
    if not blob:
        # No sentinels — try the first {...} object in the whole response.
        start, end = (text or "").find("{"), (text or "").rfind("}")
        blob = text[start:end + 1] if (start != -1 and end != -1) else ""
    blob = blob.strip().replace("```json", "").replace("```", "").strip()
    if not blob:
        return {}
    try:
        return json.loads(blob)
    except Exception:  # noqa: BLE001
        # Best-effort: grab the outermost braces.
        s, e = blob.find("{"), blob.rfind("}")
        if s != -1 and e != -1:
            try:
                return json.loads(blob[s:e + 1])
            except Exception:  # noqa: BLE001
                return {}
        return {}


def parse_rows(text: str, columns: list[str]) -> list[dict]:
    """Parse the pipe-delimited ROWS block into dicts keyed by `columns`.

    Tolerant by design: skips the header line, blank lines, markdown separators and
    any line whose cell count is wildly off (a truncated final row is simply dropped).
    """
    m = _ROWS_RE.search(text or "")
    body = m.group(1) if m else (text or "")
    rows: list[dict] = []
    header_seen = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line or "|" not in line:
            continue
        if set(line) <= {"|", "-", ":", " "}:  # markdown alignment row
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if not header_seen and (columns[0] in lowered or "profile" in lowered or "task" in lowered):
            header_seen = True
            continue
        # Need at least half the columns to treat it as a real data row.
        if len(cells) < max(3, len(columns) // 2):
            continue
        cells = (cells + [""] * len(columns))[: len(columns)]
        rows.append(dict(zip(columns, cells)))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# DETERMINISTIC RECOMPUTE + STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

def _reconcile_fab_rows(rows: list[dict], jurisdiction: str) -> tuple[list[dict], dict]:
    """Recompute every member weight in Python and return (clean_rows, stats).

    For each row: prefer the table unit weight for the profile; recompute
    weight_kg = qty x length_m x unit_weight. If that disagrees with the model's stated
    weight by more than the tolerance, we keep the computed value and flag the row.
    """
    clean: list[dict] = []
    corrected = 0
    table_hits = 0
    stated_only = 0
    for i, r in enumerate(rows, 1):
        qty = _num(r.get("qty"), 1) or 1
        length_mm = _num(r.get("length_mm"))
        length_m = length_mm / 1000.0
        model_uw = _num(r.get("unit_weight_kgm"))
        stated_wt = _num(r.get("weight_kg"))

        table_uw = W.lookup_unit_weight(r.get("profile", ""), jurisdiction)
        unit_weight = table_uw if table_uw else model_uw
        source = "table" if table_uw else ("model" if model_uw else "stated")
        if table_uw:
            table_hits += 1

        if unit_weight and length_m > 0:
            computed = W.member_weight_kg(qty, length_m, unit_weight)
        elif stated_wt:
            computed = round(stated_wt, 3)
            source = "stated"
            stated_only += 1
        else:
            computed = 0.0

        flag = (r.get("flag") or "").strip()
        if stated_wt and computed and abs(computed - stated_wt) / max(stated_wt, 1e-6) * 100 > RECONCILE_TOLERANCE_PCT:
            corrected += 1
            flag = (flag + " WEIGHT-RECONCILED").strip()

        clean.append({
            "no": i,
            "type": (r.get("type") or "").upper() or "MISC",
            "mark": r.get("mark") or "—",
            "profile": r.get("profile") or "—",
            "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
            "length_mm": round(length_mm, 1),
            "unit_weight_kgm": round(unit_weight, 2) if unit_weight else 0.0,
            "weight_kg": round(computed, 2),
            "weight_lb": W.kg_to_lb(computed),
            "grade": r.get("grade") or "—",
            "surface": r.get("surface") or "—",
            "source_sheet": r.get("source_sheet") or "—",
            "method": r.get("method") or "—",
            "weight_source": source,
            "flag": flag,
        })

    stats = {
        "rows": len(clean),
        "weights_from_table": table_hits,
        "weights_reconciled": corrected,
        "weights_stated_only": stated_only,
    }
    return clean, stats


def _category_summary(rows: list[dict]) -> list[dict]:
    """Roll the itemized rows up by member category, weights summed in Python."""
    buckets: dict[str, dict] = {}
    for r in rows:
        cat = _category_of(r["type"])
        b = buckets.setdefault(cat, {"category": cat, "count": 0, "length_m": 0.0, "weight_kg": 0.0})
        b["count"] += int(r["qty"]) if isinstance(r["qty"], int) else 1
        b["length_m"] += (r["length_mm"] / 1000.0) * (r["qty"] if isinstance(r["qty"], (int, float)) else 1)
        b["weight_kg"] += r["weight_kg"]
    out = []
    for b in buckets.values():
        out.append({
            "category": b["category"],
            "count": b["count"],
            "length_m": round(b["length_m"], 2),
            "weight_kg": round(b["weight_kg"], 2),
            "weight_lb": W.kg_to_lb(b["weight_kg"]),
            "tons": round(b["weight_kg"] / 1000.0, 3),
        })
    return sorted(out, key=lambda x: x["weight_kg"], reverse=True)


def _category_of(member_type: str) -> str:
    t = (member_type or "").upper()
    if t.startswith("W-SHAPE") or t in ("UB", "UC", "WT", "TEE", "M", "S"):
        return "W-Shapes & Beams"
    if "HSS" in t or "RHS" in t or "SHS" in t or "TUBE" in t:
        return "HSS & Tube"
    if "PIPE" in t or "CHS" in t:
        return "Pipe"
    if "ANGLE" in t or t in ("EA", "UA"):
        return "Angles"
    if "CHANNEL" in t or "PFC" in t or t == "MC-CHANNEL":
        return "Channels"
    if "PLATE" in t or "BAR" in t or "FLAT" in t:
        return "Plates & Bars"
    if "ANCHOR" in t or "BOLT" in t or "STUD" in t or "EMBED" in t:
        return "Bolts, Anchors & Embeds"
    return "Miscellaneous"


def _confidence(meta: dict, stats: dict, rfi_count: int) -> int:
    """Blend the model's self-reported confidence with objective take-off signals."""
    base = _num(meta.get("confidence"), 0)
    if base <= 0:
        base = 70  # neutral prior when the model gave nothing usable
    base = max(0.0, min(100.0, base))
    rows = max(1, stats.get("rows", 0))
    table_ratio = stats.get("weights_from_table", 0) / rows
    base += 8 * table_ratio            # more tabled weights → more trustworthy
    base -= 3 * min(rfi_count, 6)      # each open RFI erodes confidence
    reconciled_ratio = stats.get("weights_reconciled", 0) / rows
    base -= 15 * reconciled_ratio      # lots of model-arithmetic corrections → shakier source
    return int(max(5, min(99, round(base))))


# ─────────────────────────────────────────────────────────────────────────────
# MODEL RUN
# ─────────────────────────────────────────────────────────────────────────────

async def _run_model(system_prompt: str, file_pairs: list[tuple[str, str]], session_id: str) -> tuple[str, str]:
    """Upload files once, run the take-off with continuation, fall back across models."""
    client = _get_client()
    uploaded = _upload_files_sync(client, list(file_pairs))
    user_instruction = (
        "Produce the complete itemized take-off for every attached drawing/document. "
        "Emit only the META block then the ROWS block, exactly as specified."
    )
    last_err: Exception | None = None
    try:
        for model_name in MODEL_CHAIN:
            try:
                content: list = [{"type": "text", "text": user_instruction}]
                for file_id, mime in uploaded:
                    content.append(_file_block(file_id, mime))
                if content:
                    content[-1]["cache_control"] = {"type": "ephemeral"}
                text = await _generate_with_continuation(
                    client=client,
                    model_name=model_name,
                    system_prompt=system_prompt,
                    initial_content=content,
                    has_files=bool(uploaded),
                    session_id=session_id,
                    label="itemized-mto",
                )
                if text and "<<<" in text:
                    return text, engine_label(model_name)
                # No sentinels at all → treat as a soft failure and try the next tier.
                last_err = RuntimeError("Model returned no parseable take-off block")
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("itemized_mto tier %s failed: %s", model_name, e)
                continue
    finally:
        if uploaded:
            _cleanup_files(client, uploaded)
    raise RuntimeError(f"Itemized take-off failed. Last error: {last_err}")


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY
# ─────────────────────────────────────────────────────────────────────────────

async def run_itemized_mto(
    *,
    role: str,
    session_id: str,
    file_paths: Iterable[tuple[str, str]],
    jurisdiction: str = "USA",
) -> tuple[dict, str]:
    """Run the itemized take-off and return (structured_result, engine_label).

    Fabricator result carries a deterministic `tonnage` (sum of the recomputed weight
    column + accessory allowance) plus `line_items`, `category_summary`, `confidence`,
    `assumptions` and `open_rfis`. Detailer result carries `total_hours` + `line_items`.
    """
    file_pairs = list(file_paths)
    if not file_pairs:
        raise ValueError("Upload at least one drawing to run the take-off.")
    role = (role or "").lower()
    if role not in ("fabricator", "detailer"):
        raise ValueError(f"Itemized take-off supports detailer or fabricator (got '{role}').")

    system_prompt = _fab_prompt(jurisdiction) if role == "fabricator" else _det_prompt(jurisdiction)
    raw, engine = await _run_model(system_prompt, file_pairs, session_id)

    meta = parse_meta(raw)
    detected = (meta.get("jurisdiction") or jurisdiction or "USA").upper()
    if detected not in W.UNIT_WEIGHTS:
        detected = "USA"
    rfis = meta.get("open_rfis") or []
    assumptions = meta.get("assumptions") or []

    if role == "fabricator":
        rows = parse_rows(raw, FAB_COLUMNS)
        clean_rows, stats = _reconcile_fab_rows(rows, detected)
        member_weight_kg = sum(r["weight_kg"] for r in clean_rows)
        allowance = ACCESSORY_ALLOWANCE_PCT / 100.0 * member_weight_kg
        tonnage = round((member_weight_kg + allowance) / 1000.0, 2)
        result = {
            "role": "fabricator",
            "jurisdiction": detected,
            "unit_system": meta.get("unit_system", "imperial"),
            "tonnage": tonnage,
            "member_tonnage": round(member_weight_kg / 1000.0, 3),
            "accessory_allowance_pct": ACCESSORY_ALLOWANCE_PCT,
            "members_counted": len(clean_rows),
            "primary_material": meta.get("primary_material", ""),
            "drawings_seen": int(_num(meta.get("drawings_seen"))),
            "line_items": clean_rows,
            "category_summary": _category_summary(clean_rows),
            "reconciliation": stats,
            "assumptions": assumptions,
            "open_rfis": rfis,
            "confidence": _confidence(meta, stats, len(rfis)),
            "notes": meta.get("notes", ""),
        }
        if tonnage <= 0:
            raise ValueError("Could not extract a usable tonnage from the drawings.")
        return result, engine

    # ── Detailer ──────────────────────────────────────────────────────────────
    rows = parse_rows(raw, DET_COLUMNS)
    tasks: list[dict] = []
    total_hours = 0.0
    for i, r in enumerate(rows, 1):
        qty = _num(r.get("qty"), 1)
        hpu = _num(r.get("hours_per_unit"))
        hours = _num(r.get("hours")) or round(qty * hpu, 2)
        total_hours += hours
        tasks.append({
            "no": i,
            "task": r.get("task") or "—",
            "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
            "unit": r.get("unit") or "—",
            "hours_per_unit": round(hpu, 3),
            "hours": round(hours, 2),
            "basis": r.get("basis") or "—",
            "flag": (r.get("flag") or "").strip(),
        })

    drawings = int(_num(meta.get("drawings")))
    complexity = meta.get("complexity", "Medium")
    if total_hours <= 0:
        # Fallback productivity model if the model gave no hour rows.
        hpd = _DET_HRS_PER_DWG.get(complexity, 2.5)
        connections = int(_num(meta.get("connections")))
        total_hours = round(max(1, drawings) * hpd + connections * 0.35, 2)

    stats = {"rows": len(tasks)}
    result = {
        "role": "detailer",
        "jurisdiction": detected,
        "unit_system": meta.get("unit_system", "imperial"),
        "total_hours": round(total_hours, 2),
        "drawings": drawings,
        "connections": int(_num(meta.get("connections"))),
        "complexity": complexity,
        "complexity_multiplier": _num(meta.get("complexity_multiplier"), 1.0),
        "drawings_seen": int(_num(meta.get("drawings_seen"))),
        "line_items": tasks,
        "assumptions": assumptions,
        "open_rfis": rfis,
        "confidence": _confidence(meta, stats, len(rfis)),
        "notes": meta.get("notes", ""),
    }
    return result, engine
