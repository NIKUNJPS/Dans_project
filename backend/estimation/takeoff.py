"""Deterministic material take-off — the single source of project tonnage.

Division of labour:
  • Claude reads the drawings (one page batch per call) and reports every piece:
    profile, quantity, printed length, sheet, method — plus a small META block (sheets,
    grades, RFIs, reconciliation items, estimating basis).
  • Python does ALL the arithmetic: length conversion, unit weight from the profile
    designation/geometry (estimation/weights.py), weight = Qty × L × kg/m, member count
    = ΣQty, category roll-up, allowances, tonnage — so the same take-off always gives the
    same number, and the numbers in every table agree with each other by construction.

Batches are consolidated into ONE take-off: rows unioned, a piece shown both on a
schedule/shop drawing and on a plan in another batch is counted once, RFIs are
de-duplicated and renumbered, sheet coverage is rebuilt, and joists are excluded.

Model output contract (line-oriented so it survives continuation stitching):

    <<<META>>>
    { ...JSON... }
    <<<END_META>>>
    <<<ROWS>>>
    no|type|role|mark|profile|qty|raw_length|length_mm|unit_wt_kgm|weight_kg|grade|finish|sheet|detail|method|flag
    1|W-SHAPE|PRIMARY|B1|W12x26|4|24'-6"|7468|38.7|1155.7|A992|PRIMER|S-201||SCHEDULE|
    <<<END_ROWS>>>
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Iterable

from estimation import weights as W

logger = logging.getLogger(__name__)

TAKEOFF_COLUMNS = [
    "no", "type", "role", "mark", "profile", "qty", "raw_length", "length_mm",
    "unit_wt_kgm", "weight_kg", "grade", "finish", "sheet", "detail", "method", "flag",
]

# Allowances added on top of the net (itemized) member weight — shown as separate lines.
HARDWARE_WELD_ALLOWANCE_PCT = 3.0       # bolts, nuts, washers, weld metal
CONNECTION_ALLOWANCE_PCT = 5.0          # only when connection material is NOT itemized
WEIGHT_TOLERANCE_PCT = 3.0              # model vs recomputed weight before flagging
SHORT_TON_KG = 907.18474

_HARDWARE_TYPES = {"ANCHOR-ROD", "ANCHOR-BOLT", "BOLT", "STRUCTURAL-BOLT", "STUD", "WELD-STUD",
                   "SHEAR-STUD", "NUT", "WASHER", "HARDWARE"}
_PRIORITY_ORDER = {"CRITICAL": 0, "URGENT": 1, "HIGH": 1, "STANDARD": 2, "MEDIUM": 2, "LOW": 3}
_AUTHORITATIVE_METHODS = {"SHOP DWG", "SHOP", "SCHEDULE", "BOM", "BOM DIRECT"}


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

def takeoff_system_prompt(jurisdiction_hint: str = "USA") -> str:
    return f"""You are the take-off engine of STRUCTMIND — a principal structural-steel quantity surveyor with 25+ years producing fabrication take-offs for fabricators in the USA, Canada and Australia. Fabricators buy steel and price jobs from your take-off, so a missed piece or a double-counted piece costs real money. Read every attached page completely before writing anything.

WHAT YOU ARE GIVEN
The user turn lists the whole binder (every document and its page count) and names the pages attached to THIS call. Other pages of the same binder are taken off in separate calls and merged by the platform. Take off only what appears on the attached pages. Never assume what is on pages you cannot see, and never report a sheet as missing just because it is not attached here — list every sheet number called out on these pages in referenced_sheets; the platform works out what is genuinely absent from the binder.

WHAT TO TAKE OFF
Every fabricated steel piece shown: columns, beams, girders, rafters, braces, struts, purlins, girts, lintels, trusses (chords and webs as pieces when detailed), portal and moment frames, stairs (stringers, landing framing, treads), handrails and guards (posts, rails), ladders, platforms, mezzanines, canopies, embeds, base plates, cap plates, gussets, shear tabs, stiffeners, clips and all other connection plates that are shown or scheduled, crane runway beams and rails, bollards, lintels and miscellaneous steel. Hardware (anchor rods, structural bolts, shear studs) is counted, not weighed.

NEVER TAKE OFF (record each kind once in excluded_scope instead)
- Open-web steel joists, joist girders and joist bridging (SJI K / KCS / LH / DLH / SLH series, joist girders such as 48G8N10.5K, Canadian OWSJ) — they are supplied by the joist manufacturer, not the fabricator. Do not write a single row for them.
- Metal deck, reinforcing steel, precast, concrete, timber, light-gauge framing, and anything marked "by others", "NIC" or "existing".

COUNTING RULES — this is where take-offs go wrong
1. One row per distinct mark + profile + length on a source sheet; Qty carries the number of identical pieces. Never collapse different lengths into one "typical" row, and never write one row per piece when Qty can carry the count.
2. Count each physical piece exactly once. Authority order: shop/assembly drawing bill of material > member/column/brace schedule > framing plan or elevation count > grid-derived. If these pages show the same pieces on a schedule AND on a plan, take them from the schedule and do not add the plan count.
3. "TYP", "SIM", "TYPICAL AT n BAYS": multiply out to the count the drawings support and flag EST-QTY when the count is inferred rather than stated.
4. A plan that applies to several levels ("2ND–4TH FLOOR FRAMING") multiplies Qty by the number of levels — say so in assumptions.
5. Lengths: use the dimensioned length. If only grids are dimensioned, compute the length from grid spacing (method GRID-CALC; centre-line length is acceptable for beams; columns are top-of-steel minus base elevation). If a length truly cannot be determined, leave length blank and raise an RFI for it.
6. Plates and flat bar: profile is PL<thickness>x<width> as printed (USA inches, e.g. PL3/4x12; metric mm, e.g. PL20x250); the plate's length goes in the length columns; Qty is the number of plates.
7. Write profiles exactly as printed in the jurisdiction's catalogue: W12x26, HSS6x6x3/8, HSS6.625x0.280, L4x4x1/2, 2L4x3x3/8, C10x15.3, MC12x31, WT6x20, Pipe4STD; W310x52, HSS127x127x6.4, WWF900x169; 310UB46.2, 250UC89.5, 150PFC, SHS100x100x6, RHS150x100x6, CHS168.3x6.4, EA100x100x8. A built-up member is type BUILT-UP with its component plates as separate rows sharing the mark.
8. Every gap gets an RFI: a missing length, profile, quantity, grade or finish is a blank cell plus an RFI that names the mark and sheet. Never invent a value to fill a cell.

ARITHMETIC
The platform recomputes every weight from Qty × length × unit weight, so put your effort into reading profile, Qty and length correctly. Still give your unit_wt_kgm and weight_kg: they let the platform catch a misread (a disagreement over 3% is flagged for review). Detected jurisdiction for unit weights defaults to {jurisdiction_hint} unless the drawings clearly show otherwise.
{W.weight_table_text(jurisdiction_hint)}

OUTPUT — exactly two blocks, META first, with nothing before, between or after them.

<<<META>>>
{{"jurisdiction":"USA","unit_system":"imperial","structure_type":"BUILDING","standards":"...","project":{{}},"sheets":[],"referenced_sheets":[],"grades":[],"finishes":[],"material_type":{{}},"surface_treatment":{{}},"assembly_complexity":{{}},"connections_itemized":true,"assembly_method":"...","reconciliation":[],"rfis":[],"assumptions":[],"excluded_scope":[],"user_overrides":{{}},"confidence":0,"notes":"..."}}
<<<END_META>>>
<<<ROWS>>>
{"|".join(TAKEOFF_COLUMNS)}
1|W-SHAPE|PRIMARY|B1|W12x26|4|24'-6"|7468|38.7|1155.7|A992|PRIMER|S-201|3/S-501|SCHEDULE|
<<<END_ROWS>>>

META keys (valid JSON, double quotes, no trailing commas):
- jurisdiction "USA" | "CANADA" | "AUSTRALIA"; unit_system "imperial" | "metric"; structure_type "BUILDING" | "BRIDGE"; standards: governing code/standard set as one string.
- project: {{name, number, location, owner, eor, architect, contractor, fabricator, stage}} — only what title blocks on these pages show.
- sheets: [{{sheet, title, discipline, rev, doc, line_items, pieces, status}}] — EVERY sheet on these pages, steel or not; doc is the attachment label (e.g. "D1 p.12"); status READ | PARTIAL | UNREADABLE | NO STEEL.
- referenced_sheets: [sheet numbers called out on these pages].
- grades: [{{category, callout, normalized, sheet}}]; finishes: [{{scope, finish, sheet}}].
- material_type, surface_treatment, assembly_complexity: {{value, basis, sheet}} with value from — material: "Carbon Steel" | "HSS" | "Galvanised" | "Stainless" | "Weathering" | "Aluminium"; surface: "Shop primer only" | "SSPC-SP6 + 2-coat" | "SSPC-SP10 + 3-coat" | "Hot-dip galvanised" | "Intumescent fire-rated"; assembly: "Simple bolted" | "Mixed bolt/weld" | "Heavy welded" | "Architectural AESS" | "Custom curved". Use {{}} when these pages say nothing.
- connections_itemized: true when connection material (shear tabs, clips, gussets, base/cap plates) is shown or scheduled on these pages.
- assembly_method: one line on how assemblies were handled (parent + child rows, or single assembly row).
- reconciliation: [{{mark, sheet_1, value_1, sheet_2, value_2, type, impact, resolution}}] — cross-sheet disagreements on these pages; type LENGTH | QUANTITY | GRADE | PROFILE | MARK-DUPLICATE | BOM-VS-DRAWING | UNIT-SYSTEM.
- rfis: [{{id:"RFI-1", priority:"CRITICAL"|"URGENT"|"STANDARD", sheet, question, blocked_fields, expected_response}}] — one question per RFI, copy-ready, citing mark and sheet; CRITICAL = weight or length unknown, URGENT = grade/finish unknown, STANDARD = clarification.
- assumptions: [strings]; excluded_scope: [strings]; notes: one line on the take-off basis.
- user_overrides: ONLY values the user's instructions state explicitly — rate_low, rate_mid, rate_high (per ton), material_type, surface_treatment, assembly_complexity, country, tax_pct, quote_requested (true if a client-facing quote/proposal is requested). Use {{}} otherwise.
- confidence: 0-100, how completely these pages define the steel they show.

ROW RULES
- type: W-SHAPE | HSS-RECT | HSS-SQUARE | HSS-ROUND | PIPE | ANGLE | DOUBLE-ANGLE | CHANNEL | TEE | UB | UC | PFC | RHS | SHS | CHS | EA | UA | PLATE | FLAT-BAR | ROUND-BAR | GRATING | CHECKER-PLATE | BUILT-UP | ANCHOR-ROD | BOLT | STUD | MISC
- role: PRIMARY (columns, girders, beams, frames, primary bracing) | SECONDARY (purlins, girts, sub-framing, lintels, kickers, secondary bracing) | MISC (stairs, rails, ladders, platforms, embeds, bollards, canopies) | CONNECTION (plates, clips, stiffeners, gussets, base/cap plates) | HARDWARE (anchor rods, bolts, studs)
- qty integer; raw_length as printed (24'-6 3/8" or 7468); length_mm in millimetres; numbers carry no units or thousands separators; a genuine gap is an EMPTY cell (never "NF", "TBD", "N/A" or "—").
- sheet: sheet number from the title block (or the attachment page, e.g. "D1 p.12"); detail: the view/detail callout, or empty.
- method: SHOP DWG | SCHEDULE | PLAN | GRID-CALC | DETAIL | ASSUMED
- flag: space-separated codes from RECONCILE VIF ASSUMED EST-QTY UNIT-ANOMALY PROFILE-MISMATCH ASSEMBLY-PARENT ASSEMBLY-CHILD COUNT-ONLY and the RFI id (e.g. RFI-3) that tracks the row's gap; empty if none.
- Hardware rows: Qty = count, lengths and weights empty, flag COUNT-ONLY.
- Write every piece on these pages, then stop. Long output is stitched automatically, so never summarise, sample, abbreviate or stop early, and never repeat the header line."""


def takeoff_batch_header(manifest: str, batch_desc: str, batch_no: int, batch_total: int, user_text: str) -> str:
    if batch_total > 1:
        scope = (
            f"THIS CALL: batch {batch_no} of {batch_total} — {batch_desc}.\n"
            "These attachments are the only pages you can see; the other batches of the binder "
            "are taken off separately and merged by the platform."
        )
    else:
        scope = f"THIS CALL: the complete binder is attached — {batch_desc}."
    instructions = (user_text or "").strip() or "None."
    return (
        f"{manifest}\n\n{scope}\n\n"
        f"USER INSTRUCTIONS (apply where they do not conflict with the take-off rules):\n{instructions}\n\n"
        "Produce the META block and the ROWS block for the attached pages."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

_META_RE = re.compile(r"<<<META>>>(.*?)<<<END_META>>>", re.DOTALL | re.IGNORECASE)
_ROWS_RE = re.compile(r"<<<ROWS>>>(.*?)(?:<<<END_ROWS>>>|$)", re.DOTALL | re.IGNORECASE)


def num(value, default: float = 0.0) -> float:
    if value is None:
        return default
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "—", "–", "NF", "N/A", "NA", "TBD"):
        return default
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else default


def loads_lenient(blob: str) -> dict:
    """json.loads that tolerates code fences, stray prose and trailing commas."""
    if not blob:
        return {}
    s = blob.strip().replace("```json", "").replace("```", "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1:
        return {}
    s = s[a:b + 1]
    for candidate in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            out = json.loads(candidate)
            return out if isinstance(out, dict) else {}
        except Exception:  # noqa: BLE001
            continue
    return {}


def parse_meta(text: str) -> dict:
    m = _META_RE.search(text or "")
    return loads_lenient(m.group(1) if m else (text or "").split("<<<ROWS>>>")[0])


def parse_rows(text: str, columns: list[str] | None = None) -> list[dict]:
    """Pipe-delimited ROWS → dicts. Skips headers, separators and truncated fragments."""
    columns = columns or TAKEOFF_COLUMNS
    m = _ROWS_RE.search(text or "")
    body = m.group(1) if m else ""
    rows: list[dict] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or "|" not in line:
            continue
        if set(line) <= {"|", "-", ":", " "}:
            continue
        if line.startswith("|"):
            line = line[1:]
        cells = [c.strip() for c in line.split("|")]
        low = [c.lower() for c in cells]
        if columns[0] in low[:2] and ("profile" in low or "task" in low or "item_type" in low):
            continue
        if len(cells) < max(4, len(columns) // 2):
            continue
        if len(cells) > len(columns):
            # A stray '|' inside the final free-text column: fold the overflow back in.
            cells = cells[: len(columns) - 1] + [" ".join(cells[len(columns) - 1:]).strip()]
        cells = (cells + [""] * len(columns))[: len(columns)]
        rows.append(dict(zip(columns, cells)))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _clean(s) -> str:
    v = str(s or "").strip()
    return "" if v.upper() in ("—", "-", "–", "NF", "N/A", "NA", "TBD", "NONE", "NULL") else v


def _infer_type(profile: str) -> str:
    p = W.normalise_profile(profile)
    for prefix, t in (("HSS", "HSS-RECT"), ("RHS", "RHS"), ("SHS", "SHS"), ("CHS", "CHS"),
                      ("PIPE", "PIPE"), ("WT", "TEE"), ("MC", "CHANNEL"), ("W", "W-SHAPE"),
                      ("HP", "W-SHAPE"), ("2L", "DOUBLE-ANGLE"), ("L", "ANGLE"), ("EA", "EA"),
                      ("UA", "UA"), ("PL", "PLATE"), ("FL", "FLAT-BAR"), ("C", "CHANNEL")):
        if p.startswith(prefix):
            return t
    if re.match(r"^\d+UB", p):
        return "UB"
    if re.match(r"^\d+UC", p):
        return "UC"
    if re.match(r"^\d+PFC", p):
        return "PFC"
    return "MISC"


def category_of(member_type: str, profile: str, jurisdiction: str) -> str:
    t = (member_type or "").upper()
    p = W.normalise_profile(profile)
    aus = jurisdiction == "AUSTRALIA"
    if t in ("W-SHAPE", "UB", "UC", "WB", "WC") or re.match(r"^(W|HP|M|S)\d", p) or re.match(r"^\d+(UB|UC|WB|WC)", p):
        return "UB / UC / Welded Beams" if aus else "W-Shapes & Rolled Beams"
    if t in ("HSS-RECT", "HSS-SQUARE", "RHS", "SHS") or p.startswith(("RHS", "SHS", "TS")):
        return "RHS / SHS" if aus else "HSS — Rectangular & Square"
    if t in ("HSS-ROUND", "PIPE", "CHS") or p.startswith(("CHS", "PIPE")):
        return "CHS & Pipe" if aus else "HSS — Round & Pipe"
    if p.startswith("HSS"):
        return "HSS — Round & Pipe" if p.count("X") == 1 else "HSS — Rectangular & Square"
    if t in ("ANGLE", "DOUBLE-ANGLE", "EA", "UA"):
        return "EA / UA Angles" if aus else "Angles"
    if t in ("CHANNEL", "PFC", "MC-CHANNEL"):
        return "PFC / Channels" if aus else "Channels (C / MC)"
    if t in ("TEE", "WT"):
        return "Tees"
    if t in ("PLATE", "FLAT-BAR"):
        return "Plates & Flat Bar"
    if t in ("ROUND-BAR", "SQUARE-BAR", "BAR"):
        return "Bars & Rods"
    if t in ("GRATING", "CHECKER-PLATE"):
        return "Grating & Floor Plate"
    if t == "BUILT-UP":
        return "Built-up Members"
    return "Miscellaneous Steel"


_CATEGORY_ORDER = [
    "W-Shapes & Rolled Beams", "UB / UC / Welded Beams", "HSS — Rectangular & Square", "RHS / SHS",
    "HSS — Round & Pipe", "CHS & Pipe", "Angles", "EA / UA Angles", "Channels (C / MC)",
    "PFC / Channels", "Tees", "Built-up Members", "Plates & Flat Bar", "Bars & Rods",
    "Grating & Floor Plate", "Miscellaneous Steel",
]
_ROLE_ORDER = {"PRIMARY": 0, "SECONDARY": 1, "MISC": 2, "CONNECTION": 3, "HARDWARE": 4}


def _is_hardware(r: dict) -> bool:
    return (
        r.get("type", "").upper() in _HARDWARE_TYPES
        or r.get("role", "").upper() == "HARDWARE"
        or "COUNT-ONLY" in r.get("flag", "").upper()
    )


def _metric(jurisdiction: str) -> bool:
    return jurisdiction in ("CANADA", "AUSTRALIA")


def resolve_length_mm(raw: str, model_mm, jurisdiction: str) -> tuple[float, str]:
    """(length_mm, source). Printed text wins over the model's own conversion."""
    raw = _clean(raw)
    if raw:
        has_marks = any(ch in raw for ch in "'\"′″’”")
        if has_marks or _metric(jurisdiction) or re.search(r"\bmm\b|\d\s*m\b", raw, re.I):
            parsed = W.parse_length_mm(raw, jurisdiction)
            if parsed:
                return parsed, "printed"
    mm = num(model_mm)
    return (mm, "model") if mm > 0 else (0.0, "missing")


# ─────────────────────────────────────────────────────────────────────────────
# Row computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_row(raw: dict, jurisdiction: str) -> dict:
    """Recompute one take-off row deterministically."""
    profile = _clean(raw.get("profile"))
    mtype = (_clean(raw.get("type")) or _infer_type(profile)).upper()
    role = _clean(raw.get("role")).upper()
    flag = " ".join(_clean(raw.get("flag")).split())
    qty_val = num(raw.get("qty"), 0)
    est_qty = False
    if qty_val <= 0:
        qty_val, est_qty = 1, True
    qty = int(round(qty_val))
    if est_qty and "EST-QTY" not in flag:
        flag = (flag + " EST-QTY").strip()

    row = {
        "type": mtype, "role": role, "mark": _clean(raw.get("mark")) or "NO MARK",
        "profile": profile or "—", "qty": qty, "raw_length": _clean(raw.get("raw_length")),
        "grade": _clean(raw.get("grade")), "finish": _clean(raw.get("finish")),
        "sheet": _clean(raw.get("sheet")) or "—", "detail": _clean(raw.get("detail")),
        "method": _clean(raw.get("method")).upper(), "flag": flag,
    }
    if not role:
        row["role"] = "HARDWARE" if mtype in _HARDWARE_TYPES else (
            "CONNECTION" if mtype in ("PLATE", "FLAT-BAR") else "PRIMARY" if mtype in ("W-SHAPE", "UB", "UC") else "MISC")

    if _is_hardware(row):
        if "COUNT-ONLY" not in row["flag"]:
            row["flag"] = (row["flag"] + " COUNT-ONLY").strip()
        row.update(length_mm=0.0, unit_wt_kgm=0.0, weight_kg=0.0, weight_lb=0.0,
                   weight_source="count-only", hardware=True, gap=False)
        return row

    length_mm, length_src = resolve_length_mm(raw.get("raw_length"), raw.get("length_mm"), jurisdiction)
    model_uw = num(raw.get("unit_wt_kgm"))
    stated_wt = num(raw.get("weight_kg"))
    uw = W.compute_unit_weight(profile, jurisdiction)
    unit_wt, wt_source = (uw.kg_per_m, uw.method) if uw else ((model_uw, "model") if model_uw > 0 else (0.0, ""))

    if unit_wt > 0 and length_mm > 0:
        weight = qty * (length_mm / 1000.0) * unit_wt
    elif stated_wt > 0:
        weight, wt_source = stated_wt, "stated"
    else:
        weight, wt_source = 0.0, "missing"

    reconciled = False
    if stated_wt > 0 and wt_source not in ("stated", "missing") and weight > 0:
        if abs(weight - stated_wt) / max(stated_wt, 1e-9) * 100 > WEIGHT_TOLERANCE_PCT:
            reconciled = True
    row.update(
        length_mm=round(length_mm, 1), length_source=length_src,
        unit_wt_kgm=round(unit_wt, 3), weight_kg=round(weight, 2), weight_lb=round(weight * W.KG_TO_LB, 1),
        weight_source=wt_source, stated_weight_kg=round(stated_wt, 2) if stated_wt else 0.0,
        weight_reconciled=reconciled, hardware=False, gap=weight <= 0,
        is_plate=bool(uw and uw.is_plate),
    )
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Consolidation
# ─────────────────────────────────────────────────────────────────────────────

def _vote(values: Iterable[str], default: str = "") -> str:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else default


def _choose_basis(metas: list[dict], key: str) -> dict:
    """Pick the best-supported estimating-basis entry across batches."""
    entries = [m.get(key) for m in metas if isinstance(m.get(key), dict) and m.get(key, {}).get("value")]
    if not entries:
        return {}
    top = _vote(e["value"] for e in entries)
    for e in entries:
        if e["value"] == top:
            return e
    return entries[0]


_RFI_REF_RE = re.compile(r"\bRFI[-\s]?(?:MTO[-\s]?|B\d+[-\s]?)?(\d+)\b", re.I)


def consolidate(batches: list[dict], *, jurisdiction_hint: str = "USA", documents: list[dict] | None = None) -> dict:
    """Merge per-batch {batch, meta, rows} into ONE deterministic take-off."""
    metas = [b.get("meta") or {} for b in batches]
    j = _vote(((m.get("jurisdiction") or "").upper() for m in metas), "") or (jurisdiction_hint or "USA").upper()
    if j not in W.JURISDICTIONS:
        j = "USA"

    # ── RFIs: dedupe by question text, renumber by priority ─────────────────
    rfi_items: list[dict] = []
    local_map: dict[tuple[int, str], int] = {}
    seen_q: dict[str, int] = {}
    for b in batches:
        for r in (b.get("meta") or {}).get("rfis") or []:
            if not isinstance(r, dict):
                continue
            q = _clean(r.get("question"))
            if not q:
                continue
            key = re.sub(r"[^a-z0-9]", "", q.lower())[:180]
            if key in seen_q:
                idx = seen_q[key]
            else:
                idx = len(rfi_items)
                seen_q[key] = idx
                rfi_items.append({
                    "priority": (_clean(r.get("priority")) or "STANDARD").upper(),
                    "sheet": _clean(r.get("sheet")), "question": q,
                    "blocked_fields": _clean(r.get("blocked_fields")),
                    "expected_response": _clean(r.get("expected_response")),
                    "order": (b.get("batch", 0), len(rfi_items)),
                })
            lid = _clean(r.get("id"))
            m = _RFI_REF_RE.search(lid)
            if m:
                local_map[(b.get("batch", 0), m.group(1))] = idx

    # ── Rows ─────────────────────────────────────────────────────────────────
    rows: list[dict] = []
    joists: list[dict] = []
    for b in batches:
        for raw in b.get("rows") or []:
            if W.is_joist(raw.get("type"), raw.get("profile"), raw.get("mark")):
                joists.append(raw)
                continue
            row = compute_row(raw, j)
            row["batch"] = b.get("batch", 0)
            rows.append(row)

    # A piece taken from a schedule/shop drawing in one batch and counted again from a
    # plan in another batch is the classic double count: keep the authoritative rows.
    dedup_notes: list[dict] = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["mark"].upper().startswith("NO MARK") or r["hardware"]:
            continue
        groups[(_norm_key(r["mark"]), _norm_key(r["profile"]), round(r.get("length_mm", 0) / 25))].append(r)
    drop_ids: set[int] = set()
    for (_mk, _pf, _ln), grp in groups.items():
        if len({g["batch"] for g in grp}) < 2:
            continue
        auth = [g for g in grp if g["method"] in _AUTHORITATIVE_METHODS]
        plan = [g for g in grp if g["method"] not in _AUTHORITATIVE_METHODS]
        if auth and plan and {g["batch"] for g in auth}.isdisjoint({g["batch"] for g in plan}):
            for g in plan:
                drop_ids.add(id(g))
            dedup_notes.append({
                "mark": grp[0]["mark"],
                "sheet_1": ", ".join(sorted({g["sheet"] for g in auth})),
                "value_1": f"{sum(g['qty'] for g in auth)} pcs ({auth[0]['method'].title()})",
                "sheet_2": ", ".join(sorted({g["sheet"] for g in plan})),
                "value_2": f"{sum(g['qty'] for g in plan)} pcs ({(plan[0]['method'] or 'plan').title()})",
                "type": "BOM-VS-DRAWING",
                "impact": "Double count avoided",
                "resolution": "Counted once from the schedule / shop drawing; plan count not added.",
            })
    if drop_ids:
        rows = [r for r in rows if id(r) not in drop_ids]

    # ── Map row RFI references to the global numbering; open RFIs for gaps ───
    def global_id(idx: int) -> str:
        return f"RFI-MTO-{idx + 1:03d}"

    for r in rows:
        def _sub(m, batch=r["batch"]):
            idx = local_map.get((batch, m.group(1)))
            return f"__RFI{idx}__" if idx is not None else ""
        r["flag"] = " ".join(_RFI_REF_RE.sub(_sub, r["flag"]).split())

    gap_rows = [r for r in rows if r["gap"]]
    gaps_by_sheet: dict[str, list[dict]] = defaultdict(list)
    for r in gap_rows:
        if "__RFI" not in r["flag"]:
            gaps_by_sheet[r["sheet"]].append(r)
    for sheet, grp in gaps_by_sheet.items():
        marks = ", ".join(sorted({g["mark"] for g in grp}))[:400]
        missing = "length" if any(g["profile"] != "—" for g in grp) else "profile and length"
        idx = len(rfi_items)
        rfi_items.append({
            "priority": "CRITICAL", "sheet": sheet,
            "question": (f"Sheet {sheet}: confirm the {missing} of mark(s) {marks} — not dimensioned or "
                         "scheduled on the issued drawings, so the piece weight cannot be computed."),
            "blocked_fields": "Length, Est Wt", "expected_response": "Revised drawing or schedule with dimensioned lengths.",
            "order": (999, idx),
        })
        for g in grp:
            g["flag"] = (g["flag"] + f" __RFI{idx}__").strip()

    order = sorted(range(len(rfi_items)), key=lambda i: (_PRIORITY_ORDER.get(rfi_items[i]["priority"], 2), rfi_items[i]["order"]))
    renumber = {old: new for new, old in enumerate(order)}
    rfis = []
    for new, old in enumerate(order):
        item = dict(rfi_items[old])
        item.pop("order", None)
        item["id"] = global_id(new)
        if item["priority"] not in ("CRITICAL", "URGENT", "STANDARD"):
            item["priority"] = {"HIGH": "URGENT", "MEDIUM": "STANDARD", "LOW": "STANDARD"}.get(item["priority"], "STANDARD")
        rfis.append(item)
    for r in rows:
        r["flag"] = re.sub(r"__RFI(\d+)__", lambda m: global_id(renumber[int(m.group(1))]), r["flag"])

    # ── Sort + number ────────────────────────────────────────────────────────
    for r in rows:
        r["category"] = category_of(r["type"], r["profile"], j) if not r["hardware"] else "Hardware"
    cat_rank = {c: i for i, c in enumerate(_CATEGORY_ORDER)}
    rows.sort(key=lambda r: (
        r["hardware"], _ROLE_ORDER.get(r["role"], 5), cat_rank.get(r["category"], 99),
        _sheet_sort_key(r["sheet"]), _mark_sort_key(r["mark"]),
    ))
    for i, r in enumerate(rows, 1):
        r["no"] = i

    weighed = [r for r in rows if not r["hardware"]]
    hardware = [r for r in rows if r["hardware"]]

    # ── Category summary (member count = Σ Qty) ─────────────────────────────
    cats: dict[str, dict] = {}
    for r in weighed:
        c = cats.setdefault(r["category"], {"category": r["category"], "line_items": 0, "pieces": 0,
                                             "length_m": 0.0, "weight_kg": 0.0})
        c["line_items"] += 1
        c["pieces"] += r["qty"]
        c["length_m"] += r["qty"] * r["length_mm"] / 1000.0
        c["weight_kg"] += r["weight_kg"]
    categories = sorted(cats.values(), key=lambda c: cat_rank.get(c["category"], 99))
    for c in categories:
        _finish_weights(c)

    roles: dict[str, dict] = {}
    for r in weighed:
        c = roles.setdefault(r["role"] or "MISC", {"role": r["role"] or "MISC", "line_items": 0, "pieces": 0,
                                                    "length_m": 0.0, "weight_kg": 0.0})
        c["line_items"] += 1
        c["pieces"] += r["qty"]
        c["length_m"] += r["qty"] * r["length_mm"] / 1000.0
        c["weight_kg"] += r["weight_kg"]
    role_summary = sorted(roles.values(), key=lambda c: _ROLE_ORDER.get(c["role"], 9))
    for c in role_summary:
        _finish_weights(c)

    hw_counts: dict[str, int] = defaultdict(int)
    for r in hardware:
        label = " ".join(x for x in (r["type"].replace("-", " ").title(), r["profile"] if r["profile"] != "—" else "") if x)
        hw_counts[label] += r["qty"]

    # ── Totals and allowances ───────────────────────────────────────────────
    net_kg = sum(r["weight_kg"] for r in weighed)
    connection_kg = sum(r["weight_kg"] for r in weighed if r["role"] == "CONNECTION" or r["category"] == "Plates & Flat Bar")
    connections_itemized = any(bool(m.get("connections_itemized")) for m in metas) or (
        net_kg > 0 and connection_kg / net_kg >= 0.02)
    allowances = []
    if not connections_itemized and net_kg > 0:
        allowances.append({
            "label": "Connection material allowance (connections not detailed on the drawings)",
            "pct": CONNECTION_ALLOWANCE_PCT, "kg": net_kg * CONNECTION_ALLOWANCE_PCT / 100.0,
        })
    allowances.append({
        "label": "Bolts, washers & weld metal allowance",
        "pct": HARDWARE_WELD_ALLOWANCE_PCT, "kg": net_kg * HARDWARE_WELD_ALLOWANCE_PCT / 100.0,
    })
    gross_kg = net_kg + sum(a["kg"] for a in allowances)

    largest = max(weighed, key=lambda r: (r["weight_kg"] / max(r["qty"], 1)), default=None)

    # ── Sheets / coverage ───────────────────────────────────────────────────
    sheet_info: dict[str, dict] = {}
    for m in metas:
        for s in m.get("sheets") or []:
            if not isinstance(s, dict):
                continue
            key = _norm_key(s.get("sheet"))
            if not key:
                continue
            prev = sheet_info.get(key, {})
            sheet_info[key] = {
                "sheet": _clean(s.get("sheet")) or prev.get("sheet", ""),
                "title": _clean(s.get("title")) or prev.get("title", ""),
                "discipline": _clean(s.get("discipline")) or prev.get("discipline", ""),
                "rev": _clean(s.get("rev")) or prev.get("rev", ""),
                "doc": _clean(s.get("doc")) or prev.get("doc", ""),
                "status": (_clean(s.get("status")) or prev.get("status", "READ")).upper(),
            }
    for r in rows:
        key = _norm_key(r["sheet"])
        if key and key not in sheet_info:
            sheet_info[key] = {"sheet": r["sheet"], "title": "", "discipline": "", "rev": "", "doc": "", "status": "READ"}
    for info in sheet_info.values():
        srows = [r for r in rows if _norm_key(r["sheet"]) == _norm_key(info["sheet"])]
        info["line_items"] = len(srows)
        info["pieces"] = sum(r["qty"] for r in srows if not r["hardware"])
        info["weight_kg"] = round(sum(r["weight_kg"] for r in srows), 1)
        gaps = sum(1 for r in srows if r["gap"])
        if srows and info["status"] in ("NO STEEL", ""):
            info["status"] = "READ"
        if gaps:
            info["status"] = "PARTIAL"
        info["gaps"] = gaps
    sheets = sorted(sheet_info.values(), key=lambda s: _sheet_sort_key(s["sheet"]))
    seen_keys = set(sheet_info)
    referenced = set()
    for m in metas:
        for s in m.get("referenced_sheets") or []:
            k = _norm_key(s)
            if k and k not in seen_keys and len(k) <= 12:
                referenced.add(_clean(s))
    referenced_missing = sorted(referenced, key=_sheet_sort_key)

    # ── Reconciliation, assumptions, scope ──────────────────────────────────
    reconciliation = []
    for m in metas:
        for item in m.get("reconciliation") or []:
            if isinstance(item, dict) and _clean(item.get("mark")):
                reconciliation.append({k: _clean(item.get(k)) for k in (
                    "mark", "sheet_1", "value_1", "sheet_2", "value_2", "type", "impact", "resolution")})
    reconciliation.extend(dedup_notes)

    def _union(key: str) -> list[str]:
        out, seen = [], set()
        for m in metas:
            for a in m.get(key) or []:
                a = _clean(a if isinstance(a, str) else json.dumps(a))
                k = re.sub(r"[^a-z0-9]", "", a.lower())
                if a and k not in seen:
                    seen.add(k)
                    out.append(a)
        return out

    excluded_scope = _union("excluded_scope")
    joist_note = ("Open-web steel joists, joist girders and joist bridging — excluded "
                  "(supplied by the joist manufacturer).")
    if joists or any("joist" in e.lower() for e in excluded_scope):
        excluded_scope = [e for e in excluded_scope if "joist" not in e.lower()] + [joist_note]

    def _union_dicts(key: str, fields: tuple[str, ...]) -> list[dict]:
        out, seen = [], set()
        for m in metas:
            for d in m.get(key) or []:
                if not isinstance(d, dict):
                    continue
                item = {f: _clean(d.get(f)) for f in fields}
                k = "|".join(item.values()).lower()
                if any(item.values()) and k not in seen:
                    seen.add(k)
                    out.append(item)
        return out

    project: dict = {}
    for m in metas:
        for k, v in (m.get("project") or {}).items():
            if _clean(v) and not project.get(k):
                project[k] = _clean(v)

    overrides: dict = {}
    for m in metas:
        for k, v in (m.get("user_overrides") or {}).items():
            if v not in (None, "", [], {}) and k not in overrides:
                overrides[k] = v

    weight_methods = Counter(r["weight_source"] for r in weighed)
    conf_vals = [num(m.get("confidence")) for m in metas if num(m.get("confidence")) > 0]
    stats = {
        "line_items": len(rows),
        "weighed_line_items": len(weighed),
        "pieces": sum(r["qty"] for r in weighed),
        "hardware_items": sum(r["qty"] for r in hardware),
        "weights_by_method": dict(weight_methods),
        "weights_reconciled": sum(1 for r in weighed if r.get("weight_reconciled")),
        "lengths_from_print": sum(1 for r in weighed if r.get("length_source") == "printed"),
        "gap_rows": len(gap_rows),
        "joists_excluded": sum(int(num(x.get("qty"), 1) or 1) for x in joists),
        "double_counts_removed": len(drop_ids),
        "batches": len(batches),
    }

    return {
        "jurisdiction": j,
        "unit_system": _vote((m.get("unit_system") or "").lower() for m in metas) or ("metric" if _metric(j) else "imperial"),
        "structure_type": _vote((m.get("structure_type") or "").upper() for m in metas) or "BUILDING",
        "standards": _vote(_clean(m.get("standards")) for m in metas),
        "project": project,
        "documents": documents or [],
        "sheets": sheets,
        "referenced_missing": referenced_missing,
        "rows": rows,
        "categories": categories,
        "roles": role_summary,
        "hardware": [{"item": k, "qty": v} for k, v in sorted(hw_counts.items())],
        "totals": {
            "net_kg": round(net_kg, 2),
            "allowances": [{**a, "kg": round(a["kg"], 2)} for a in allowances],
            "gross_kg": round(gross_kg, 2),
            "net_t": round(net_kg / 1000.0, 3),
            "gross_t": round(gross_kg / 1000.0, 3),
            "gross_short_tons": round(gross_kg / SHORT_TON_KG, 3),
            "gross_lb": round(gross_kg * W.KG_TO_LB, 0),
            "pieces": stats["pieces"],
            "line_items": len(rows),
        },
        "connections_itemized": connections_itemized,
        "largest_piece": ({
            "mark": largest["mark"], "profile": largest["profile"],
            "kg": round(largest["weight_kg"] / max(largest["qty"], 1), 1),
            "lb": round(largest["weight_kg"] / max(largest["qty"], 1) * W.KG_TO_LB),
            "length_mm": largest["length_mm"],
        } if largest and largest["weight_kg"] > 0 else None),
        "rfis": rfis,
        "reconciliation": reconciliation,
        "assumptions": _union("assumptions"),
        "excluded_scope": excluded_scope,
        "grades": _union_dicts("grades", ("category", "callout", "normalized", "sheet")),
        "finishes": _union_dicts("finishes", ("scope", "finish", "sheet")),
        "assembly_method": _vote(_clean(m.get("assembly_method")) for m in metas),
        "estimating_basis": {
            "material_type": _choose_basis(metas, "material_type"),
            "surface_treatment": _choose_basis(metas, "surface_treatment"),
            "assembly_complexity": _choose_basis(metas, "assembly_complexity"),
        },
        "user_overrides": overrides,
        "notes": _vote(_clean(m.get("notes")) for m in metas),
        "confidence": round(sum(conf_vals) / len(conf_vals)) if conf_vals else 0,
        "stats": stats,
    }


def _finish_weights(c: dict) -> None:
    c["length_m"] = round(c["length_m"], 2)
    c["length_ft"] = round(c["length_m"] / W.FT_TO_M, 1)
    c["weight_lb"] = round(c["weight_kg"] * W.KG_TO_LB, 0)
    c["tonnes"] = round(c["weight_kg"] / 1000.0, 3)
    c["short_tons"] = round(c["weight_kg"] / SHORT_TON_KG, 3)
    c["weight_kg"] = round(c["weight_kg"], 1)


def _sheet_sort_key(s: str):
    parts = re.split(r"(\d+)", str(s or ""))
    return [int(p) if p.isdigit() else p.upper() for p in parts]


def _mark_sort_key(s: str):
    return _sheet_sort_key(s)


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers (shared by every take-off-driven report)
# ─────────────────────────────────────────────────────────────────────────────

def round_half_up(x: float, ndigits: int = 0) -> float:
    """Commercial rounding (0.5 always rounds up), unlike Python's banker's round()."""
    from decimal import ROUND_HALF_UP, Decimal

    q = Decimal(1).scaleb(-ndigits)
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def fmt(v, decimals: int = 1) -> str:
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if decimals == 0:
        return f"{f:,.0f}"
    return f"{f:,.{decimals}f}"


def md_table(header: list[str], rows: list[list], align: list[str] | None = None) -> str:
    def cell(x) -> str:
        s = "—" if x is None or str(x).strip() == "" else str(x)
        return s.replace("|", "/").replace("\n", " ")
    align = align or ["---"] * len(header)
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def ton_label(jurisdiction: str) -> tuple[str, str]:
    """(pricing ton unit key, label) — US practice prices per short ton (2,000 lb)."""
    if (jurisdiction or "USA").upper() == "USA":
        return "short_tons", "short ton"
    return "tonnes", "tonne"


def ton_definition(jurisdiction: str) -> str:
    return "1 short ton = 2,000 lb (907.18 kg)" if (jurisdiction or "USA").upper() == "USA" else "1 tonne = 1,000 kg"


def pricing_tons(takeoff: dict) -> float:
    key, _ = ton_label(takeoff.get("jurisdiction", "USA"))
    t = takeoff.get("totals", {})
    return t.get("gross_short_tons", 0.0) if key == "short_tons" else t.get("gross_t", 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# MTO report
# ─────────────────────────────────────────────────────────────────────────────

def render_mto_report(t: dict) -> str:
    j = t["jurisdiction"]
    imperial = j == "USA"
    tot = t["totals"]
    parts: list[str] = []

    # ── OUTPUT 1 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 1 — PRE-EXTRACTION SUMMARY")
    if t.get("documents"):
        parts.append("Table: Binder Register")
        parts.append(md_table(
            ["#", "Document", "Pages", "Size (MB)", "Status"],
            [[f"D{d['index']}", d["name"], d["pages"], fmt(d.get("size_mb"), 2), "Read in full"] for d in t["documents"]],
            [":--", ":--", "--:", "--:", ":--"],
        ))
    proj = t.get("project") or {}
    facts = [
        f"- Project: {proj.get('name') or '—'}" + (f" ({proj['number']})" if proj.get("number") else ""),
        f"- Detected jurisdiction: {j} · Structure type: {t.get('structure_type', 'BUILDING')}",
        f"- Standard set applied: {t.get('standards') or '—'}",
        f"- Unit system: {t.get('unit_system', '—')}",
        f"- Assembly handling: {t.get('assembly_method') or 'Main member as parent row; plates, clips and stiffeners as child rows sharing the mark.'}",
        f"- Sheets referenced on the drawings but not in the binder: {', '.join(t['referenced_missing']) or 'None'}",
    ]
    parts.append("\n".join(facts))
    parts.append("Table: Per-Sheet Extraction Coverage")
    parts.append(md_table(
        ["Source Sheet", "Title", "Line Items", "Pieces", "Weight (kg)", "Status"],
        [[s["sheet"], s.get("title") or "—", s["line_items"], s["pieces"], fmt(s["weight_kg"]), s["status"]]
         for s in t["sheets"]],
        [":--", ":--", "--:", "--:", "--:", ":-:"],
    ))
    covered = sum(1 for s in t["sheets"] if s["line_items"])
    parts.append(
        f"Sheets reviewed: {len(t['sheets'])} · Sheets with steel: {covered} · "
        f"Line items: {tot['line_items']:,} · Pieces (Σ Qty): {tot['pieces']:,}"
    )

    # ── OUTPUT 2 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 2 — COMPLETE MTO TABLE")
    parts.append("Table: Fabrication-Grade Material Take-Off (weights recomputed: Qty × Length × Unit Wt)")
    header = ["#", "Type", "Role", "Mark", "Profile", "Qty", "Raw Length", "Length (mm)", "Unit Wt (kg/m)",
              "Est Wt (kg)", "Est Wt (lbs)", "Grade", "Finish", "Source Sheet", "View/Detail", "Method", "Flag"]
    body = []
    for r in t["rows"]:
        raw_len = r.get("raw_length") or (W.format_ft_in(r["length_mm"]) if imperial and r.get("length_mm") else "")
        body.append([
            r["no"], r["type"], r["role"].title(), r["mark"], r["profile"], r["qty"], raw_len,
            fmt(r["length_mm"], 0) if r.get("length_mm") else "—",
            fmt(r["unit_wt_kgm"], 2) if r.get("unit_wt_kgm") else "—",
            fmt(r["weight_kg"]) if not r["hardware"] and r["weight_kg"] else "—",
            fmt(r["weight_lb"], 0) if not r["hardware"] and r["weight_kg"] else "—",
            r["grade"], r["finish"], r["sheet"], r["detail"], r["method"].title() if r["method"] else "",
            (r["flag"] + (" WT-RECALC" if r.get("weight_reconciled") else "")).strip(),
        ])
    parts.append(md_table(header, body, [":--", ":--", ":--", ":--", ":--", "--:", ":--", "--:", "--:", "--:",
                                         "--:", ":--", ":--", ":--", ":--", ":--", ":--"]))
    parts.append(
        f"Total line items: {tot['line_items']:,} · Total pieces (Σ Qty, excl. hardware): {tot['pieces']:,} · "
        f"Net member weight: {fmt(tot['net_kg'])} kg"
    )

    # ── OUTPUT 3 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 3 — MTO SUMMARY BY CATEGORY")
    parts.append("Table: Tonnage by Member Category (Member Count = Σ Qty)")
    cat_rows = [[c["category"], c["line_items"], c["pieces"], fmt(c["length_m"], 1), fmt(c["length_ft"], 0),
                 fmt(c["weight_kg"]), fmt(c["weight_lb"], 0), fmt(c["tonnes"], 3), fmt(c["short_tons"], 3)]
                for c in t["categories"]]
    net = tot["net_kg"]
    cat_rows.append(["**NET MEMBER TOTAL**", sum(c["line_items"] for c in t["categories"]),
                     sum(c["pieces"] for c in t["categories"]),
                     fmt(sum(c["length_m"] for c in t["categories"]), 1),
                     fmt(sum(c["length_ft"] for c in t["categories"]), 0),
                     fmt(net), fmt(net * W.KG_TO_LB, 0), fmt(net / 1000, 3), fmt(net / SHORT_TON_KG, 3)])
    parts.append(md_table(
        ["Category", "Line Items", "Member Count (pcs)", "Total Length (m)", "Total Length (ft)",
         "Weight (kg)", "Weight (lbs)", "Metric Tonnes (t)", "Short Tons"],
        cat_rows, [":--", "--:", "--:", "--:", "--:", "--:", "--:", "--:", "--:"],
    ))

    parts.append("Table: Tonnage by Structural Role")
    parts.append(md_table(
        ["Role", "Line Items", "Member Count (pcs)", "Weight (kg)", "Metric Tonnes (t)", "Short Tons", "Share"],
        [[c["role"].title(), c["line_items"], c["pieces"], fmt(c["weight_kg"]), fmt(c["tonnes"], 3),
          fmt(c["short_tons"], 3), f"{(c['weight_kg'] / net * 100) if net else 0:.1f}%"] for c in t["roles"]],
        [":--", "--:", "--:", "--:", "--:", "--:", "--:"],
    ))

    parts.append("Table: Project Tonnage Build-Up")
    build = [["Net member weight (sum of Output 2)", "—", fmt(net), fmt(net * W.KG_TO_LB, 0),
              fmt(net / 1000, 3), fmt(net / SHORT_TON_KG, 3)]]
    for a in tot["allowances"]:
        build.append([a["label"], f"{a['pct']:.1f}%", fmt(a["kg"]), fmt(a["kg"] * W.KG_TO_LB, 0),
                      fmt(a["kg"] / 1000, 3), fmt(a["kg"] / SHORT_TON_KG, 3)])
    g = tot["gross_kg"]
    build.append(["**TOTAL FABRICATED WEIGHT**", "—", f"**{fmt(g)}**", f"**{fmt(g * W.KG_TO_LB, 0)}**",
                  f"**{fmt(g / 1000, 3)}**", f"**{fmt(g / SHORT_TON_KG, 3)}**"])
    parts.append(md_table(["Item", "Allowance", "Weight (kg)", "Weight (lbs)", "Metric Tonnes (t)", "Short Tons"],
                          build, [":--", "--:", "--:", "--:", "--:", "--:"]))
    headline = (f"{fmt(tot['gross_short_tons'], 2)} short tons ({fmt(tot['gross_t'], 2)} t)" if imperial
                else f"{fmt(tot['gross_t'], 2)} t ({fmt(tot['gross_short_tons'], 2)} short tons)")
    lines = [f"- Project fabricated tonnage: **{headline}** — {tot['pieces']:,} pieces in {tot['line_items']:,} line items."]
    if t.get("largest_piece"):
        lp = t["largest_piece"]
        lines.append(f"- Heaviest single piece: {lp['mark']} · {lp['profile']} · {fmt(lp['kg'])} kg · {fmt(lp['lb'], 0)} lbs")
    if t["hardware"]:
        lines.append("- Hardware (counted, not weighed — covered by the bolts & weld allowance): " +
                     "; ".join(f"{h['item']} × {h['qty']:,}" for h in t["hardware"]))
    if t["excluded_scope"]:
        lines.append("- Excluded from this take-off: " + "; ".join(t["excluded_scope"]))
    parts.append("\n".join(lines))

    # ── OUTPUT 4 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 4 — RECONCILIATION REGISTER")
    if t["reconciliation"]:
        parts.append(md_table(
            ["Mark/Tag", "Sheet 1", "Value on Sheet 1", "Sheet 2", "Value on Sheet 2", "Type", "Impact", "Resolution Needed"],
            [[r["mark"], r["sheet_1"], r["value_1"], r["sheet_2"], r["value_2"], r["type"], r["impact"], r["resolution"]]
             for r in t["reconciliation"]],
        ))
    else:
        parts.append("No reconciliation items.")
    recalc = [r for r in t["rows"] if r.get("weight_reconciled")]
    if recalc:
        parts.append("Table: Weight Verification — drawing/BOM weight differs from the computed weight by more than 3 %")
        parts.append(md_table(
            ["#", "Mark", "Profile", "Qty", "Length (mm)", "Stated Wt (kg)", "Computed Wt (kg)", "Difference"],
            [[r["no"], r["mark"], r["profile"], r["qty"], fmt(r["length_mm"], 0), fmt(r["stated_weight_kg"]),
              fmt(r["weight_kg"]), f"{(r['weight_kg'] - r['stated_weight_kg']) / r['stated_weight_kg'] * 100:+.1f}%"]
             for r in recalc[:200]],
            [":--", ":--", ":--", "--:", "--:", "--:", "--:", "--:"],
        ))
        parts.append("Computed weights (published unit weight × length × Qty) are used in every total; "
                     "confirm the flagged profiles and lengths against the shop drawings.")

    # ── OUTPUT 5 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 5 — RFI PACKAGE")
    parts.append(render_rfis(t["rfis"], j))

    # ── Basis ───────────────────────────────────────────────────────────────
    parts.append("## WEIGHT BASIS & ASSUMPTIONS")
    wm = t["stats"]["weights_by_method"]
    basis = [
        "- Every weight is computed: Est Wt = Qty × Length × unit weight. Rolled shapes take their weight from the "
        "designation (W/C/MC/WT lb/ft; metric W, UB/UC kg/m); HSS, pipe, angles and plates are computed from section "
        "geometry on nominal thickness at 7,850 kg/m³ (490 lb/ft³) — the basis of the AISC / CISC / AS-NZS tables.",
        f"- Weight sources: designation {wm.get('designation', 0):,} · geometry {wm.get('geometry', 0):,} · "
        f"catalogue {wm.get('table', 0):,} · drawing/BOM stated {wm.get('stated', 0):,} · "
        f"model unit weight {wm.get('model', 0):,} · not computable {wm.get('missing', 0):,} (RFI raised).",
        f"- Lengths converted from the printed dimension: {t['stats']['lengths_from_print']:,} of "
        f"{t['stats']['weighed_line_items']:,} line items.",
    ]
    if t["stats"].get("double_counts_removed"):
        basis.append(f"- {t['stats']['double_counts_removed']} plan-count line item(s) were not added because the same "
                     "pieces are taken from a schedule or shop drawing (see Output 4).")
    basis += [f"- {a}" for a in t["assumptions"]]
    parts.append("\n".join(basis))
    return "\n\n".join(parts)


def render_rfis(rfis: list[dict], jurisdiction: str) -> str:
    if not rfis:
        return "No RFIs required."
    blocks = []
    for r in rfis:
        blocks.append("\n".join([
            f"**{r['id']}**",
            f"- Priority: {r['priority']}",
            f"- Jurisdiction: {jurisdiction}",
            f"- Sheet Reference: {r.get('sheet') or '—'}",
            f"- Blocking Fields: {r.get('blocked_fields') or '—'}",
            f"- Question: {r['question']}",
            f"- Expected Response: {r.get('expected_response') or 'Revised drawing or written confirmation.'}",
        ]))
    counts = Counter(r["priority"] for r in rfis)
    blocks.append(
        f"Total RFIs: {len(rfis)} (Critical {counts.get('CRITICAL', 0)} | Urgent {counts.get('URGENT', 0)} | "
        f"Standard {counts.get('STANDARD', 0)})"
    )
    return "\n\n".join(blocks)
