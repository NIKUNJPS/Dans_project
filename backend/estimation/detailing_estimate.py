"""Estimation Pro v2 — hours-based detailing estimate computed from the MTO take-off.

There is no separate counting pass. The take-off (estimation/takeoff.py) already carries,
for every row, the member code, the complexity factor codes and the Output 2 group the
drawings support, and in its META the `assemblies` (stairs, rails, ladders, gates,
frames) and the `estimate_inputs` (erection sheets, delegated connections, completeness
checklist). This module maps that take-off to detailing effort deterministically:

    row / assembly → item code (BASE_HOURS)          member first, then type / profile
    → multiplier = Π FACTORS, capped at ×2.50          unless a cap reason is recorded
    → one line per (item, factor set, group)           Adj = base × mult (0.01 h)
    → Low / High = Adj × Qty × 0.90 / 1.10              subtotal to 0.1 h
    → completeness tier → risk buffer → LOCK hours = subtotal × (1 + buffer %), half-up to the hour
    → LOCK cost = LOCK hours × confidential band, half-up to the 100
    → task split (last row absorbs rounding) → consistency checks.

All arithmetic is Decimal with commercial (half-up) rounding, so the manifest, the
summary, the itemized table, the task split and the cost conversion print the same
numbers by construction. The rate band lives only in estimation/secure_rates.py and is
read once, inside the cost step; neither the estimate dict nor the report carries a rate
("[CONFIDENTIAL]" is printed in its place).

`detailing_system_prompt()` returns the 2C rule card — these rules as text, used as the
mode-registry prompt for Estimation Pro. `parse_batch` / `consolidate` remain as thin
shims that price v1 piece-count batches with the same engine.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from estimation import secure_rates
from estimation import weights as W
from estimation.takeoff import loads_lenient, md_table, num, parse_rows, render_rfis, round_half_up as rhu

# ─── Rule tables ─────────────────────────────────────────────────────────────

# code → (label, base hours per unit, unit). Identical in every region.
BASE_HOURS: dict[str, tuple[str, float, str]] = {
    "BEAM_SIMPLE":       ("Beam — simple (< 30 ft)", 2.50, "ea"),
    "BEAM_STANDARD":     ("Beam — standard (30–50 ft)", 3.50, "ea"),
    "BEAM_COMPLEX":      ("Beam — complex (> 50 ft)", 5.00, "ea"),
    "COLUMN_LIGHT":      ("Column — light", 3.50, "ea"),
    "COLUMN_HEAVY":      ("Column — heavy (W12+ / HP12+ or > 24 ft)", 5.00, "ea"),
    "POST_SHORT":        ("Post — short (< 6 ft)", 1.50, "ea"),
    "BRACE_VERTICAL":    ("Brace — vertical", 2.00, "ea"),
    "BRACE_KNEE":        ("Brace — knee brace", 1.25, "ea"),
    "STAIR_STRAIGHT":    ("Stair — straight", 6.00, "stair"),
    "STAIR_SWITCHBACK":  ("Stair — switchback", 8.00, "stair"),
    "STAIR_TREADS":      ("Stair — treads / nosings", 0.50, "flight"),
    "RAIL_STRAIGHT":     ("Handrail — straight", 1.50, "10 ft"),
    "RAIL_CURVED":       ("Handrail — curved / returns", 2.50, "10 ft"),
    "GUARDRAIL":         ("Guardrail", 1.75, "10 ft"),
    "LADDER":            ("Ladder", 1.00, "10 ft"),
    "MISC_PLATE":        ("Misc — plate / embed / base / cap plate", 0.75, "ea"),
    "MISC_CLIP":         ("Misc — clip / shear tab / stiffener / connection angle", 0.25, "ea"),
    "MISC_LINTEL":       ("Misc — lintel / kicker / sag rod", 0.40, "ea"),
    "BOLLARD":           ("Bollard", 0.75, "ea"),
    "GATE_LEAF":         ("Gate leaf", 2.50, "leaf"),
    "BENT_PLATE":        ("Bent plate / ledger / deck angle", 0.40, "20 ft"),
    "ROOF_SCREEN":       ("Roof screen / dunnage frame", 3.00, "frame"),
    "CANOPY_FRAME":      ("Canopy frame (members not itemized)", 8.00, "frame"),
    "ERECTION_SHEET":    ("Erection drawing", 2.00, "sheet"),
    "CONNECTION_DESIGN": ("Connection design (delegated)", 3.00, "type"),
}
_UNIT_FT = {"RAIL_STRAIGHT": 10, "RAIL_CURVED": 10, "GUARDRAIL": 10, "LADDER": 10, "BENT_PLATE": 20}

# code → (label, multiplier). The combined multiplier is the product, capped at FACTOR_CAP.
FACTORS: dict[str, tuple[str, float]] = {
    "HSS":       ("HSS / pipe section", 1.10),
    "AESS":      ("AESS", 1.30),
    "SKEW":      ("Skewed geometry", 1.35),
    "SLOPE":     ("Sloped / warped", 1.15),
    "ATYP":      ("Atypical connection", 1.25),
    "GALV":      ("Galvanizing", 1.15),
    "SEISMIC":   ("Seismic detailing", 1.25),
    "MOMENT1":   ("Moment connection, one end", 1.40),
    "DELEGATED": ("Delegated connection design", 1.20),
    "SPLICE":    ("Bolted field splice", 1.10),
}
FACTOR_CAP = 2.50
_FACTOR_ALIASES = {
    "MOMENT": "MOMENT1", "MOMENT_1": "MOMENT1", "MOMENT_ONE": "MOMENT1", "MOMENTONE": "MOMENT1",
    "HDG": "GALV", "GALVANIZED": "GALV", "GALVANISED": "GALV", "SKEWED": "SKEW", "SKEW_GE15": "SKEW",
    "SKEW_LT15": "SKEW", "SLOPED": "SLOPE", "ATYPICAL": "ATYP", "FIELD_SPLICE": "SPLICE", "TUBE": "HSS",
    "SEISMIC_DETAILING": "SEISMIC", "DELEGATED_DESIGN": "DELEGATED",
}
_HSS_PREFIXES = ("HSS", "PIPE", "TS", "RHS", "SHS", "CHS")
_GALV_RE = re.compile(r"GALV|HDG|HOT[\s-]?DIP", re.I)

CHECKLIST = [
    ("qty_counted", "All member quantities explicitly counted on the drawings."),
    ("connections_detailed", "Connection types detailed or called out (not assumed throughout)."),
    ("grades_finishes", "Material grades and finishes specified for all items."),
    ("drawings_current", "Drawings recent (IFC or equivalent) and internally coordinated."),
    ("no_missing_info", "No significant missing, conflicting or illegible information."),
    ("assumptions_documented", "All assumptions reasonable and documented."),
]
_NOT_CONFIRMED = "not confirmed on the drawings"

# (task, % standard, % when SEISMIC / MOMENT1 lines carry > 20 % of the hours, note)
TASK_SPLIT = [
    ("Modeling", 22.5, 22.5, "3D model build"),
    ("Shop Drawings / Editing", 42.5, 40.0, "Assembly and single-part drawings"),
    ("Checking", 17.5, 20.0, "Independent check and back-check"),
    ("Erection Drawings", 7.5, 7.5, "Erection / GA sheets"),
    ("RFIs / Revisions", 5.0, 5.0, "RFI log and revision cycles"),
    ("PM / Coordination", 5.0, 5.0, "Coordination and submittals"),
]
_HEAVY_SHARE = Decimal("0.20")

GROUP_LABELS = {
    "COLUMNS": "Columns & Posts", "ROOF": "Roof Framing", "FLOOR": "Floor Framing", "CANOPY": "Canopy",
    "STAIRS": "Stairs & Rails", "SECONDARY": "Secondary Building", "MISC": "Misc & Site Metals",
    "CONNECTION": "Connection Material", "HARDWARE": "Hardware", "UNGROUPED": "Ungrouped",
    "GENERAL": "Project-wide",
}
_GROUP_ORDER = ["COLUMNS", "ROOF", "FLOOR", "CANOPY", "STAIRS", "SECONDARY", "MISC", "CONNECTION"]

# ─── Member classes ──────────────────────────────────────────────────────────

MEMBER_CODES = (
    "COLUMN POST BEAM GIRDER HEADER LINTEL BRACE KNEE-BRACE KICKER COLLECTOR PURLIN GIRT TRUSS "
    "MOMENT-FRAME CANOPY-FRAME STAIR-STRINGER STAIR-TREAD STAIR-LANDING HANDRAIL GUARDRAIL LADDER "
    "BOLLARD GATE GATE-POST EMBED LEDGER BENT-PLATE PLATE BASE-PLATE CAP-PLATE SHEAR-TAB STIFFENER "
    "CLIP CONNECTION-ANGLE DECK-ANGLE DUNNAGE ROOF-SCREEN SAG-ROD ANCHOR-ROD BOLT STUD HATCH-FRAME "
    "DOOR-FRAME HOIST-BEAM OVERHEAD-DOOR-SUPPORT FALL-ANCHOR MISC"
).split()
_BEAM_MEMBERS = {"BEAM", "GIRDER", "HEADER", "COLLECTOR", "PURLIN", "GIRT", "HOIST-BEAM", "OVERHEAD-DOOR-SUPPORT",
                 "DOOR-FRAME", "HATCH-FRAME", "MOMENT-FRAME", "TRUSS"}
_COLUMN_MEMBERS = {"COLUMN", "POST"}
_DIRECT_ITEMS = {
    "BRACE": "BRACE_VERTICAL", "KNEE-BRACE": "BRACE_KNEE",
    "KICKER": "MISC_LINTEL", "LINTEL": "MISC_LINTEL", "SAG-ROD": "MISC_LINTEL",
    "PLATE": "MISC_PLATE", "EMBED": "MISC_PLATE", "BASE-PLATE": "MISC_PLATE", "CAP-PLATE": "MISC_PLATE",
    "FALL-ANCHOR": "MISC_PLATE",
    "CLIP": "MISC_CLIP", "SHEAR-TAB": "MISC_CLIP", "STIFFENER": "MISC_CLIP", "CONNECTION-ANGLE": "MISC_CLIP",
    "BOLLARD": "BOLLARD",
}
_BENT_MEMBERS = {"BENT-PLATE", "LEDGER", "DECK-ANGLE"}
_HARDWARE_MEMBERS = {"ANCHOR-ROD", "BOLT", "STUD"}
# Members priced through an assembly, never per row.
_ASSEMBLY_KIND_OF = {
    "STAIR-STRINGER": "STAIR", "STAIR-TREAD": "STAIR", "STAIR-LANDING": "STAIR",
    "HANDRAIL": "HANDRAIL", "GUARDRAIL": "GUARDRAIL", "LADDER": "LADDER",
    "GATE": "GATE", "GATE-POST": "GATE", "DUNNAGE": "DUNNAGE_FRAME", "ROOF-SCREEN": "ROOF_SCREEN",
    "CANOPY-FRAME": "CANOPY_FRAME",
}
ASSEMBLY_KINDS = ("STAIR", "LADDER", "GATE", "HANDRAIL", "GUARDRAIL", "CANOPY_FRAME", "DUNNAGE_FRAME", "ROOF_SCREEN")
_KIND_ALIASES = {
    "STAIRS": "STAIR", "STAIRCASE": "STAIR", "STAIRWAY": "STAIR", "RAIL": "HANDRAIL", "RAILS": "HANDRAIL",
    "HANDRAILS": "HANDRAIL", "RAILING": "HANDRAIL", "GUARD_RAIL": "GUARDRAIL", "GUARDRAILS": "GUARDRAIL",
    "LADDERS": "LADDER", "GATES": "GATE", "DUNNAGE": "DUNNAGE_FRAME", "DUNNAGE_FRAMES": "DUNNAGE_FRAME",
    "CANOPY": "CANOPY_FRAME", "CANOPY_FRAMES": "CANOPY_FRAME", "SCREEN": "ROOF_SCREEN", "ROOF_SCREENS": "ROOF_SCREEN",
}
_KIND_DEFAULT_GROUP = {"STAIR": "STAIRS", "HANDRAIL": "STAIRS", "GUARDRAIL": "STAIRS", "LADDER": "STAIRS",
                       "GATE": "MISC", "DUNNAGE_FRAME": "ROOF", "ROOF_SCREEN": "ROOF", "CANOPY_FRAME": "CANOPY"}
_KIND_NAME = {"STAIR": "Stair", "HANDRAIL": "Handrail", "GUARDRAIL": "Guardrail", "LADDER": "Ladder", "GATE": "Gate",
              "DUNNAGE_FRAME": "Dunnage frame", "ROOF_SCREEN": "Roof screen", "CANOPY_FRAME": "Canopy frame"}
# Pipe / tube is the standard build of rails and ladders — the base hours already cover it.
_NO_HSS_KINDS = {"HANDRAIL", "GUARDRAIL", "LADDER"}

_MEMBER_COMPACT = {c.replace("-", ""): c for c in MEMBER_CODES}
_MEMBER_ALIASES = {
    "COL": "COLUMN", "STRINGER": "STAIR-STRINGER", "STAIR": "STAIR-STRINGER", "TREAD": "STAIR-TREAD",
    "LANDING": "STAIR-LANDING", "RAIL": "HANDRAIL", "RAILING": "HANDRAIL", "GUARD": "GUARDRAIL",
    "ANCHORBOLT": "ANCHOR-ROD", "ANCHOR": "ANCHOR-ROD", "HEADEDSTUD": "STUD", "SHEARSTUD": "STUD",
    "WELDSTUD": "STUD", "PL": "PLATE", "BASEPL": "BASE-PLATE", "CAPPL": "CAP-PLATE", "EMBEDPLATE": "EMBED",
    "BENTPL": "BENT-PLATE", "SHEARPLATE": "SHEAR-TAB", "FINPLATE": "SHEAR-TAB", "KNEE": "KNEE-BRACE",
    "BRACING": "BRACE", "MISCELLANEOUS": "MISC",
}
# Type / description keywords → member, first hit wins (used only when `member` is blank or unknown).
_KEYWORD_MEMBERS = [(re.compile(p, re.I), m) for p, m in (
    (r"\bKNEE[\s-]?BRAC", "KNEE-BRACE"), (r"\bKICKER", "KICKER"), (r"\bSAG[\s-]?RODS?\b", "SAG-ROD"),
    (r"\bBASE[\s-]?(PL|PLATE)S?\b", "BASE-PLATE"), (r"\bCAP[\s-]?(PL|PLATE)S?\b", "CAP-PLATE"),
    (r"\bEMBED", "EMBED"), (r"\bSHEAR[\s-]?TABS?\b|\bFIN[\s-]?PLATES?\b", "SHEAR-TAB"), (r"\bSTIFFENER", "STIFFENER"),
    (r"\bCONNECTION[\s-]?ANGLES?\b", "CONNECTION-ANGLE"), (r"\bCLIPS?\b", "CLIP"),
    (r"\bBENT[\s-]?(PL|PLATE)S?\b", "BENT-PLATE"), (r"\bLEDGER", "LEDGER"),
    (r"\bDECK[\s-]?ANGLES?\b|\bPOUR[\s-]?STOPS?\b", "DECK-ANGLE"), (r"\bLINTEL", "LINTEL"),
    (r"\bSTRINGER", "STAIR-STRINGER"), (r"\bTREADS?\b", "STAIR-TREAD"), (r"\bLANDINGS?\b", "STAIR-LANDING"),
    (r"\bGUARD[\s-]?RAIL", "GUARDRAIL"), (r"\bHAND[\s-]?RAIL|\bRAILINGS?\b", "HANDRAIL"), (r"\bLADDER", "LADDER"),
    (r"\bBOLLARD", "BOLLARD"), (r"\bGATE[\s-]?POSTS?\b", "GATE-POST"), (r"\bGATES?\b", "GATE"),
    (r"\bDUNNAGE", "DUNNAGE"), (r"\bROOF[\s-]?SCREEN|\bSCREEN[\s-]?WALL", "ROOF-SCREEN"),
    (r"\bCANOPY[\s-]?FRAME", "CANOPY-FRAME"), (r"\bANCHOR[\s-]?(RODS?|BOLTS?)\b", "ANCHOR-ROD"),
    (r"\b(HEADED|SHEAR|WELD(ED)?)[\s-]?STUDS?\b", "STUD"), (r"\bBOLTS?\b", "BOLT"),
    (r"\bBRAC(E|ES|ING)\b", "BRACE"), (r"\bCOLUMNS?\b|\bCOL\b", "COLUMN"), (r"\bPOSTS?\b", "POST"),
    (r"\bGIRDERS?\b", "GIRDER"), (r"\bPURLINS?\b", "PURLIN"), (r"\bGIRTS?\b", "GIRT"),
    (r"\bHEADERS?\b", "HEADER"), (r"\bTRUSS", "TRUSS"), (r"\bBEAMS?\b", "BEAM"), (r"\b(PL|PLATES?)\b", "PLATE"),
)]
_PLATE_PROFILE_PREFIXES = ("PL", "FB", "FLAT", "BPL", "CHECKER", "BENTPL", "STIFFPL", "CAPPL")
_W_DEPTH_RE = re.compile(r"^(W|HP)(\d+(?:\.\d+)?)X")
_AUS_DEPTH_RE = re.compile(r"^(\d+)(UBP|UB|UC|WB|WC)")
_EST_FLAGS = ("EST-QTY", "EST-SIZE", "EST-LEN")
_RUNG_RE = re.compile(r"\b(RUNGS?|CAGE|HOOPS?)\b", re.I)
_CURVED_RE = re.compile(r"\bCURV", re.I)
_ERECTION_TITLE_RE = re.compile(r"\b(PLANS?|FRAMING|ELEVATIONS?)\b", re.I)
_STRUCT_SHEET_RE = re.compile(r"^S[DF]?[-.\s]?\d", re.I)

_REGION_ALIASES = {"US": "USA", "USA": "USA", "UNITEDSTATES": "USA", "CA": "CANADA", "CAN": "CANADA",
                   "CANADA": "CANADA", "AU": "AUSTRALIA", "AUS": "AUSTRALIA", "AUSTRALIA": "AUSTRALIA"}
_BENCHMARKS = {
    "USA": "AISC Steel Construction Manual, NISD detailing standards, internal historical data.",
    "CANADA": "CISC Handbook of Steel Construction, CSA S16, CWB / CSA W47.1 conventions, internal historical data.",
    "AUSTRALIA": "AS 4100, AS/NZS 5131 (CC1-CC4), ASI detailing conventions, internal historical data.",
}
_QUOTE_STANDARD = {
    "USA": "All work to AISC / NISD practice with senior QC.",
    "CANADA": "All work to CSA S16 and CWB conventions with senior QC.",
    "AUSTRALIA": "All work to AS 4100 / AS/NZS 5131 with senior QC.",
}
_QUOTE_RE = re.compile(r"\b(client[- ]facing|quot(e|es|ation|ations)|proposals?)\b", re.I)
_NEGATION_RE = re.compile(r"\b(no|not|without|don'?t|do not|never|skip|exclude|omit)\b[^.;:\n]{0,25}$", re.I)
_SHOW_MAX = 12          # sheets / marks listed per line before "+N more"
_KEEP_MAX = 200         # marks kept per line in the dict (huge take-offs)

_C01, _C1, _ONE = Decimal("0.01"), Decimal("0.1"), Decimal("1")
_LOW_F, _HIGH_F = Decimal("0.90"), Decimal("1.10")


# ─── Small helpers ───────────────────────────────────────────────────────────

def _d(x) -> Decimal:
    return Decimal(str(x))


def _q(x: Decimal, step: Decimal) -> Decimal:
    return x.quantize(step, rounding=ROUND_HALF_UP)


def _txt(v) -> str:
    """One clean line of text (None → '')."""
    return " ".join(str(v).split()) if v is not None else ""


def _f(v, default: float = 0.0) -> float:
    """Finite float from a number or printed value; NaN / inf / bool → default."""
    if v is None or isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else default
    f = num(v, default)
    return f if math.isfinite(f) else default


def _int(v, default: int = 0) -> int:
    f = _f(v, float(default))
    return int(_q(_d(f), _ONE))


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    return _txt(v).lower() in ("true", "yes", "y", "1", "met", "x", "ok")


def _region(value) -> str | None:
    return _REGION_ALIASES.get(re.sub(r"[^A-Z]", "", str(value or "").upper()))


def _feet_mm(mm) -> float:
    f = _f(mm)
    return round(f / 304.8, 3) if f > 0 else 0.0


def _feet(v) -> float:
    """Assembly length in feet: 42, '42', '42 ft', 42'-6\", '12.5 m', '3,600 mm'."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = _f(v)
        return f if f > 0 else 0.0
    s = _txt(v)
    if not s:
        return 0.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|lf)?\.?", s.replace(",", ""), re.I)
    if m:
        return float(m.group(1))
    mm = W.parse_length_mm(s, "USA")
    return round(mm / 304.8, 3) if mm and mm > 0 else max(0.0, _f(s))


def _units(feet: float, per: int) -> int:
    return math.ceil(round(feet / per, 6)) if feet > 0 else 0


def _add_unique(dst: list, values, limit: int | None = None) -> None:
    seen = set(dst)
    for v in values:
        v = _txt(v)
        if v and v not in seen and v not in ("—", "-", "NO MARK"):
            if limit is not None and len(dst) >= limit:
                return
            dst.append(v)
            seen.add(v)


def _show(values: list[str], total: int | None = None, limit: int = _SHOW_MAX) -> str:
    total = len(values) if total is None else total
    shown = ", ".join(values[:limit])
    return shown + (f" (+{total - limit} more)" if total > limit else "")


def _h1(x) -> str:
    return f"{float(x):,.1f}"


def _h2(x) -> str:
    return f"{float(x):,.2f}"


# ─── Row → item mapping ──────────────────────────────────────────────────────

def _canon_member(raw) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", _txt(raw).upper())
    if not compact:
        return ""
    for c in (compact, compact[:-1] if compact.endswith("S") else ""):
        if c and (c in _MEMBER_COMPACT or c in _MEMBER_ALIASES):
            return _MEMBER_COMPACT.get(c) or _MEMBER_ALIASES[c]
    return ""


def _member_of(row: dict) -> tuple[str, bool]:
    """(member code, inferred?) — the model's member first, then type / description keywords."""
    code = _canon_member(row.get("member"))
    if code:
        return code, False
    text = f"{_txt(row.get('type'))} {_txt(row.get('description'))}"
    for rx, member in _KEYWORD_MEMBERS:
        if rx.search(text):
            return member, True
    return "MISC", bool(_txt(row.get("member")))


def _factor_codes(raw) -> list[str]:
    tokens = raw if isinstance(raw, (list, tuple, set)) else re.split(r"[+,;/\s]+", _txt(raw))
    out: list[str] = []
    for tok in tokens:
        t = re.sub(r"[^A-Z0-9_]", "", _txt(tok).upper().replace("-", "_"))
        t = _FACTOR_ALIASES.get(t, t)
        if t in FACTORS and t not in out:
            out.append(t)
    return out


def _ordered(codes) -> tuple[str, ...]:
    s = set(codes)
    return tuple(c for c in FACTORS if c in s)


def _row_factors(row: dict) -> tuple[str, ...]:
    codes = _factor_codes(row.get("factors"))
    if W.normalise_profile(_txt(row.get("profile"))).startswith(_HSS_PREFIXES):
        codes.append("HSS")
    if _GALV_RE.search(_txt(row.get("finish"))):
        codes.append("GALV")
    return _ordered(codes)


def _is_plate(row: dict) -> bool:
    if row.get("is_plate") is True:
        return True
    typ = _txt(row.get("type")).upper()
    if "PLATE" in typ or typ in ("PL", "FLAT-BAR", "FLAT BAR", "FB"):
        return True
    return W.normalise_profile(_txt(row.get("profile"))).startswith(_PLATE_PROFILE_PREFIXES)


def _nominal_depth_in(profile: str) -> float | None:
    p = W.normalise_profile(profile)
    m = _W_DEPTH_RE.match(p)
    if m:
        d = float(m.group(2))
        return d / 25.4 if d > 48 else d          # W310x52 (mm) vs W12x26 (in)
    m = _AUS_DEPTH_RE.match(p)
    return float(m.group(1)) / 25.4 if m else None


def _beam_item(ft: float) -> tuple[str, str]:
    if ft <= 0:
        return "BEAM_STANDARD", "length not stated — standard class assumed"
    if ft < 30:
        return "BEAM_SIMPLE", ""
    return ("BEAM_STANDARD", "") if ft <= 50 else ("BEAM_COMPLEX", "")


def _column_item(profile: str, ft: float) -> tuple[str, str]:
    if 0 < ft < 6:
        return "POST_SHORT", ""
    depth = _nominal_depth_in(profile)
    if (depth is not None and depth >= 12 - 1e-6) or ft > 24:
        return "COLUMN_HEAVY", ""
    return "COLUMN_LIGHT", "" if ft > 0 else "length not stated — light class assumed"


def _is_hardware(row: dict, member: str) -> bool:
    return row.get("hardware") is True or _txt(row.get("group")).upper() == "HARDWARE" or member in _HARDWARE_MEMBERS


def _flags(row: dict) -> set[str]:
    out = set(_txt(row.get("flag")).upper().split())
    fl = row.get("flags")
    if isinstance(fl, (list, tuple, set)):
        out.update(_txt(x).upper() for x in fl)
    return out


# ─── Pieces (pre-aggregation) ────────────────────────────────────────────────

@dataclass
class _Piece:
    item: str
    group: str
    qty: int = 0
    feet: float = 0.0                       # length-priced items: Σ ft, units = ceil(Σ / unit ft)
    factors: tuple[str, ...] = ()
    sheets: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)
    kind: str = "member"                    # member | assembly | derived | project | legacy
    est_qty: int = 0
    est_feet: float = 0.0


def _row_ctx(row: dict, idx: int) -> dict:
    sheet = _txt(row.get("sheet"))
    return {
        "no": _int(row.get("no"), idx + 1) or idx + 1,
        "group": _txt(row.get("group")).upper() or "UNGROUPED",
        "mark": _txt(row.get("mark")),
        "sheet": sheet if sheet not in ("—", "-") else "",
        "profile": _txt(row.get("profile")),
        "qty": _int(row.get("qty")),
        "ft": _feet_mm(row.get("length_mm")),
        "factors": _row_factors(row),
        "est": bool(_flags(row) & set(_EST_FLAGS)),
        "text": f"{_txt(row.get('type'))} {_txt(row.get('description'))}",
    }


def _map_rows(rows: list[dict]) -> tuple[list[_Piece], dict[int, str], dict[int, dict], dict[int, str], list[str]]:
    """Member rows → pieces. Returns (pieces, status by row index, row context, assembly kind by row, assumptions)."""
    pieces: list[_Piece] = []
    status: dict[int, str] = {}
    ctx: dict[int, dict] = {}
    kind_of: dict[int, str] = {}
    unknown_len = {"BEAM": 0, "COLUMN": 0, "BENT": 0}
    for i, row in enumerate(rows):
        member, inferred = _member_of(row)
        c = ctx[i] = _row_ctx(row, i)
        c["member"] = member
        if _is_hardware(row, member):
            status[i] = "HARDWARE"
            continue
        if W.is_joist(row.get("type"), row.get("profile"), row.get("mark"), row.get("member")):
            status[i] = "JOIST"
            continue
        if c["qty"] <= 0:
            status[i] = "NO QTY"
            continue
        if member in _ASSEMBLY_KIND_OF:
            status[i] = "PENDING"
            kind_of[i] = _ASSEMBLY_KIND_OF[member]
            continue
        note = "member inferred from type / description" if inferred else ""
        feet = 0.0
        qty = c["qty"]
        if member in _BEAM_MEMBERS:
            item, why = _beam_item(c["ft"])
            unknown_len["BEAM"] += bool(why)
        elif member in _COLUMN_MEMBERS:
            item, why = _column_item(c["profile"], c["ft"])
            unknown_len["COLUMN"] += bool(why)
        elif member in _DIRECT_ITEMS:
            item, why = _DIRECT_ITEMS[member], ""
        elif member in _BENT_MEMBERS:
            item, qty = "BENT_PLATE", 0
            if c["ft"] > 0:
                feet, why = c["qty"] * c["ft"], ""
            else:
                feet, why = c["qty"] * 20.0, "length not stated — 20 ft per piece assumed"
                unknown_len["BENT"] += 1
        else:
            item = "MISC_PLATE" if _is_plate(row) else "MISC_LINTEL"
            why = "mapped by type"
        pieces.append(_Piece(
            item=item, group=c["group"], qty=qty, feet=feet, factors=c["factors"], sheets=[c["sheet"]],
            marks=[c["mark"]], subs=[c["profile"]], notes=[n for n in (note, why) if n], rows=[c["no"]],
            est_qty=qty if c["est"] else 0, est_feet=feet if c["est"] else 0.0,
        ))
        status[i] = "LINE"
    assumptions = []
    if unknown_len["BEAM"]:
        assumptions.append(f"Beam length class — assumed standard (30–50 ft) on {unknown_len['BEAM']} line item(s) "
                           "because no length is stated")
    if unknown_len["COLUMN"]:
        assumptions.append(f"Column class — assumed light on {unknown_len['COLUMN']} line item(s) "
                           "because no length is stated and the section is not W12+ / HP12+")
    if unknown_len["BENT"]:
        assumptions.append(f"Bent plate / ledger / deck angle length — assumed 20 ft per piece on {unknown_len['BENT']} "
                           "line item(s) because no length is stated")
    return pieces, status, ctx, kind_of, assumptions


# ─── Assemblies ──────────────────────────────────────────────────────────────

def _kind(raw) -> str:
    k = re.sub(r"[\s-]+", "_", _txt(raw).upper())
    k = _KIND_ALIASES.get(k, k)
    return k if k in ASSEMBLY_KINDS else ""


def _norm_assembly(a: dict) -> dict | None:
    kind = _kind(a.get("kind"))
    if not kind:
        return None
    qty = _int(a.get("qty"), 1)
    group = _txt(a.get("group")).upper()
    return {
        "kind": kind, "mark": _txt(a.get("mark")), "sheet": _txt(a.get("sheet")), "qty": qty if qty > 0 else 1,
        "flights": _int(a.get("flights")), "length_ft": _feet(a.get("length_ft")), "leaves": _int(a.get("leaves")),
        "frames": _int(a.get("frames")), "stair_type": _txt(a.get("stair_type")).upper(),
        "curved": _truthy(a.get("curved")), "group": group, "factors": _factor_codes(a.get("factors")),
    }


def _inherited_factors(kind: str, idxs: list[int], ctx: dict[int, dict], extra=()) -> tuple[str, ...]:
    codes = list(extra)
    for i in idxs:
        codes.extend(ctx[i]["factors"])
    if kind in _NO_HSS_KINDS:
        codes = [c for c in codes if c != "HSS"]
    return _ordered(codes)


def _common_group(idxs: list[int], ctx: dict[int, dict], default: str) -> str:
    votes = Counter(ctx[i]["group"] for i in idxs if ctx[i]["group"] != "UNGROUPED")
    return votes.most_common(1)[0][0] if votes else default


def _itemized_groups(status: dict[int, str], ctx: dict[int, dict]) -> set[str]:
    """Groups holding itemized beam / column lines (a canopy priced per member, not per frame)."""
    return {ctx[i]["group"] for i, s in status.items()
            if s == "LINE" and ctx[i]["member"] in (_BEAM_MEMBERS | _COLUMN_MEMBERS)}


def _assembly_pieces(raw_assemblies, status: dict[int, str], ctx: dict[int, dict],
                     kind_of: dict[int, str]) -> tuple[list[_Piece], list[str], list[dict]]:
    """Assemblies (and row-derived fallbacks) → pieces. Updates `status` for every assembly-priced row."""
    pieces: list[_Piece] = []
    assumptions: list[str] = []
    risks: list[dict] = []
    itemized = _itemized_groups(status, ctx)
    by_kind: dict[str, list[dict]] = {}
    for a in raw_assemblies or []:
        na = _norm_assembly(a) if isinstance(a, dict) else None
        if na:
            by_kind.setdefault(na["kind"], []).append(na)
    rows_of: dict[str, list[int]] = {}
    for i, k in kind_of.items():
        rows_of.setdefault(k, []).append(i)

    for kind in ASSEMBLY_KINDS:
        idxs = rows_of.get(kind, [])
        items = by_kind.get(kind, [])
        made: list[_Piece] = []
        covered_by = "ASSEMBLY"
        if items:
            for a in items:
                matched = _match_rows(a, idxs, ctx, single=len(items) == 1)
                made += _pieces_for_assembly(kind, a, matched, ctx, itemized, assumptions)
            marks = {a["mark"].upper() for a in items if a["mark"]}
            loose = sorted({ctx[i]["mark"] for i in idxs
                            if ctx[i]["mark"] and ctx[i]["mark"].upper() not in marks and ctx[i]["mark"] != "NO MARK"})
            if marks and loose:
                risks.append({"risk": f"{_KIND_NAME[kind]} rows {_show(loose, limit=6)} are not tied to an assembly mark — "
                                      "confirm they are inside the assembly quantities", "hours": 0.0, "sheet": ""})
        elif idxs:
            made, note = _derive_pieces(kind, idxs, ctx, itemized)
            if note:
                assumptions.append(note)
        if kind == "CANOPY_FRAME" and not any(p.qty or p.feet for p in made):
            covered_by = "ITEMIZED"
        priced = any(p.qty or p.feet for p in made)
        for i in idxs:
            itemized_here = kind == "CANOPY_FRAME" and ctx[i]["group"] in itemized
            status[i] = covered_by if (priced or itemized_here) else "UNPRICED"
        pieces += [p for p in made if p.qty or p.feet]
    return pieces, assumptions, risks


def _match_rows(a: dict, idxs: list[int], ctx: dict[int, dict], *, single: bool) -> list[int]:
    if a["mark"]:
        m = [i for i in idxs if ctx[i]["mark"].upper() == a["mark"].upper()]
        if m:
            return m
    if a["sheet"]:
        m = [i for i in idxs if ctx[i]["sheet"].upper() == a["sheet"].upper()]
        if m:
            return m
    return list(idxs) if single else []


def _pieces_for_assembly(kind: str, a: dict, matched: list[int], ctx: dict[int, dict], itemized: set[str],
                         assumptions: list[str]) -> list[_Piece]:
    # The rows' Output 2 group wins (the take-off stamps a default group on every assembly);
    # the assembly's own group applies only when no row is tied to it.
    fallback = a["group"] if a["group"] and a["group"] != "HARDWARE" else _KIND_DEFAULT_GROUP[kind]
    group = _common_group(matched, ctx, fallback)
    factors = _inherited_factors(kind, matched, ctx, a["factors"])
    sheets = [a["sheet"]] + [ctx[i]["sheet"] for i in matched]
    name = a["mark"] or _KIND_NAME[kind]
    marks = [a["mark"]] if a["mark"] else [ctx[i]["mark"] for i in matched]
    rows = [ctx[i]["no"] for i in matched]
    qty = a["qty"]
    notes: list[str] = []

    def piece(item: str, n: int, sub: str) -> _Piece:
        return _Piece(item=item, group=group, qty=n, factors=factors, sheets=list(sheets), marks=list(marks),
                      subs=[sub], notes=list(notes), rows=list(rows), kind="assembly")

    if kind == "STAIR":
        switch = "SWITCH" in a["stair_type"] or "SCISSOR" in a["stair_type"]
        flights = a["flights"]
        if flights <= 0:
            flights = 2 if switch else 1
            assumptions.append(f"{name} — assumed {flights} flight(s) per stair because no flight count is stated")
        if a["curved"]:
            notes.append("curved geometry noted — confirm")
        sub = f"{name} {'switchback' if switch else 'straight'} ×{qty}, {flights} flight(s) each"
        return [piece("STAIR_SWITCHBACK" if switch else "STAIR_STRAIGHT", qty, sub),
                piece("STAIR_TREADS", qty * flights, sub)]
    if kind in ("HANDRAIL", "GUARDRAIL", "LADDER"):
        item = {"HANDRAIL": "RAIL_CURVED" if a["curved"] else "RAIL_STRAIGHT", "GUARDRAIL": "GUARDRAIL",
                "LADDER": "LADDER"}[kind]
        if a["curved"] and kind != "HANDRAIL":
            notes.append("curved geometry noted — confirm")
        per = _units(a["length_ft"], 10)
        if per <= 0:
            per = 1
            assumptions.append(f"{name} — assumed 10 ft per item because no length is stated")
        return [piece(item, per * qty, f"{name} {a['length_ft']:g} ft ×{qty}" if a["length_ft"] else f"{name} ×{qty}")]
    if kind == "GATE":
        leaves = a["leaves"] if a["leaves"] > 0 else 1
        return [piece("GATE_LEAF", leaves * qty, f"{name} ×{qty}, {leaves} leaf/leaves each")]
    frames = a["frames"] if a["frames"] > 0 else 1
    if kind == "CANOPY_FRAME":
        if group in itemized:
            assumptions.append(f"{name} — assumed included in the itemized canopy members because beams / columns "
                               "of that group are listed individually")
            return []
        return [piece("CANOPY_FRAME", frames * qty, f"{name} ×{qty}, {frames} frame(s) each")]
    return [piece("ROOF_SCREEN", frames * qty, f"{name} ×{qty}, {frames} frame(s) each")]   # DUNNAGE_FRAME / ROOF_SCREEN


def _distinct_marks(idxs: list[int], ctx: dict[int, dict]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i in idxs:
        m = ctx[i]["mark"]
        key = m.upper() if m and m.upper() != "NO MARK" else f"#ROW{ctx[i]['no']}"
        out.setdefault(key, []).append(i)
    return out


def _derive_pieces(kind: str, idxs: list[int], ctx: dict[int, dict], itemized: set[str]) -> tuple[list[_Piece], str]:
    """Fallback when the take-off lists rows of an assembly member but no assembly of that kind."""
    note = "derived from MTO rows — ASSUMED"
    by_group: dict[str, list[int]] = {}
    for i in idxs:
        by_group.setdefault(ctx[i]["group"], []).append(i)
    pieces: list[_Piece] = []
    told = ""

    def piece(item: str, group: str, gidx: list[int], qty: int = 0, feet: float = 0.0, factors=None, sub="") -> _Piece:
        return _Piece(item=item, group=group, qty=qty, feet=feet,
                      factors=factors if factors is not None else _inherited_factors(kind, gidx, ctx),
                      sheets=[ctx[i]["sheet"] for i in gidx], marks=[ctx[i]["mark"] for i in gidx],
                      subs=[sub] if sub else [ctx[i]["profile"] for i in gidx], notes=[note],
                      rows=[ctx[i]["no"] for i in gidx], kind="derived",
                      est_qty=qty if any(ctx[i]["est"] for i in gidx) else 0)

    for group, gidx in by_group.items():
        if kind == "STAIR":
            stringers = [i for i in gidx if ctx[i]["member"] == "STAIR-STRINGER"]
            n = len(_distinct_marks(stringers, ctx)) or 1
            pieces += [piece("STAIR_STRAIGHT", group, gidx, qty=n, sub=f"{n} stair(s), one per stringer mark"),
                       piece("STAIR_TREADS", group, gidx, qty=n, sub=f"{n} flight(s), one per stair")]
            told = "Stairs — assumed one straight stair (one flight) per stringer mark because no stair assembly is listed"
        elif kind in ("HANDRAIL", "GUARDRAIL"):
            for i in gidx:
                c = ctx[i]
                item = "GUARDRAIL" if kind == "GUARDRAIL" else ("RAIL_CURVED" if _CURVED_RE.search(c["text"]) else "RAIL_STRAIGHT")
                feet = c["qty"] * c["ft"] / 2.0 if c["ft"] > 0 else 10.0
                pieces.append(piece(item, group, [i], feet=feet))
            told = (f"{_KIND_NAME[kind]}s — assumed rail length = Σ (qty × length) ÷ 2 rails because no "
                    f"{_KIND_NAME[kind].lower()} assembly is listed")
        elif kind == "LADDER":
            rails = [i for i in gidx if not _RUNG_RE.search(ctx[i]["text"]) and ctx[i]["ft"] > 0]
            for i in rails:
                pieces.append(piece("LADDER", group, [i], feet=ctx[i]["qty"] * ctx[i]["ft"] / 2.0))
            if not rails:
                n = len(_distinct_marks(gidx, ctx)) or 1
                pieces.append(piece("LADDER", group, gidx, qty=n, sub=f"{n} ladder(s) × one 10 ft unit"))
            told = "Ladders — assumed ladder length = Σ side-rail length ÷ 2 because no ladder assembly is listed"
        elif kind == "GATE":
            leaves_rows = [i for i in gidx if ctx[i]["member"] == "GATE"]
            if leaves_rows:
                leaves = sum(max(ctx[i]["qty"] for i in m) for m in _distinct_marks(leaves_rows, ctx).values())
            else:
                leaves = max(1, math.ceil(sum(ctx[i]["qty"] for i in gidx) / 2))
            pieces.append(piece("GATE_LEAF", group, gidx, qty=leaves, sub=f"{leaves} leaf/leaves"))
            told = "Gates — assumed one leaf per gate mark × qty because no gate assembly is listed"
        elif kind == "CANOPY_FRAME" and group in itemized:
            continue
        else:   # DUNNAGE_FRAME / ROOF_SCREEN / CANOPY_FRAME
            n = len(_distinct_marks(gidx, ctx)) or 1
            item = "CANOPY_FRAME" if kind == "CANOPY_FRAME" else "ROOF_SCREEN"
            pieces.append(piece(item, group, gidx, qty=n, sub=f"{n} frame(s), one per mark"))
            told = f"{_KIND_NAME[kind]}s — assumed one frame per mark because no {_KIND_NAME[kind].lower()} assembly is listed"
    for p in pieces:
        if p.feet and p.est_qty:
            p.est_feet, p.est_qty = p.feet, 0
    return pieces, told


# ─── Project-wide lines ──────────────────────────────────────────────────────

def _erection_sheets(takeoff: dict) -> tuple[int, list[str]]:
    """Structural plan / framing / elevation sheets (register first, else the sheet list); min 1."""
    found: list[str] = []
    seen: set[str] = set()
    entries = [e for e in (takeoff.get("register") or []) if isinstance(e, dict)]
    if not entries:
        entries = [e for e in (takeoff.get("sheets") or []) if isinstance(e, dict)]
    for e in entries:
        sheet, title = _txt(e.get("sheet")), _txt(e.get("title"))
        disc = _txt(e.get("discipline")).upper()
        state = (_txt(e.get("coverage")) or _txt(e.get("status"))).upper()
        structural = disc.startswith("STRUCT") or (not disc and bool(_STRUCT_SHEET_RE.match(sheet)))
        if not structural or state == "NO STEEL" or not _ERECTION_TITLE_RE.search(title):
            continue
        key = sheet.upper() or _txt(e.get("ref")).upper() or title.upper()
        if key not in seen:
            seen.add(key)
            found.append(sheet or _txt(e.get("ref")) or title)
    return max(1, len(found)), found


# ─── Shared estimate engine ──────────────────────────────────────────────────

def _aggregate(pieces: list[_Piece]) -> list[dict]:
    acc: dict[tuple, dict] = {}
    for p in pieces:
        if p.item not in BASE_HOURS:
            continue
        key = (p.item, p.factors, p.group)
        a = acc.get(key)
        if a is None:
            a = acc[key] = {"item": p.item, "factors": p.factors, "group": p.group, "qty": 0, "feet": 0.0,
                            "sheets": [], "marks": [], "mark_count": set(), "subs": [], "notes": [], "rows": [],
                            "kinds": [], "est_qty": 0, "est_feet": 0.0}
        a["qty"] += p.qty
        a["feet"] += p.feet
        a["est_qty"] += p.est_qty
        a["est_feet"] += p.est_feet
        _add_unique(a["sheets"], p.sheets)
        _add_unique(a["marks"], p.marks, _KEEP_MAX)
        a["mark_count"].update(m for m in (_txt(x) for x in p.marks) if m and m not in ("—", "NO MARK"))
        _add_unique(a["subs"], p.subs, _KEEP_MAX)
        _add_unique(a["notes"], p.notes)
        a["rows"].extend(p.rows)
        _add_unique(a["kinds"], [p.kind])
    lines = []
    for a in acc.values():
        per = _UNIT_FT.get(a["item"])
        qty = a["qty"] + (_units(a["feet"], per) if per else 0)
        if qty <= 0:
            continue
        a["qty"] = qty
        a["est_qty"] += _units(a["est_feet"], per) if per else 0
        lines.append(a)
    rank = {g: i for i, g in enumerate(_GROUP_ORDER)}
    items = list(BASE_HOURS)
    lines.sort(key=lambda a: (a["group"] == "GENERAL", rank.get(a["group"], len(rank)), a["group"],
                              items.index(a["item"]), list(FACTORS).index(a["factors"][0]) if a["factors"] else -1,
                              len(a["factors"]), a["factors"]))
    return lines


def _price(lines: list[dict], cap_reason: str, labels: dict[str, str], metric: bool) -> list[dict]:
    out = []
    for a in lines:
        label, base_h, unit = BASE_HOURS[a["item"]]
        raw = math.prod((_d(FACTORS[c][1]) for c in a["factors"]), start=_ONE)
        capped = not cap_reason and raw > _d(FACTOR_CAP)
        mult = _d(FACTOR_CAP) if capped else raw
        base = _d(base_h)
        adj = _q(base * mult, _C01)
        low = _q(adj * a["qty"] * _LOW_F, _C01)
        high = _q(adj * a["qty"] * _HIGH_F, _C01)
        per = _UNIT_FT.get(a["item"])
        unit_txt = f"{unit} ({per * 0.3048:.2f} m)" if metric and per else unit
        out.append({
            "item": a["item"], "label": label, "unit": unit_txt, "group": a["group"],
            "group_label": labels.get(a["group"]) or GROUP_LABELS.get(a["group"]) or a["group"].title() or "—",
            "sub_type": _show(a["subs"], limit=3).replace(", ", " · "),
            "qty": a["qty"], "feet": round(a["feet"], 2) if per else 0.0,
            "base": float(base), "factors": list(a["factors"]),
            "raw_multiplier": float(_q(raw, Decimal("0.0001"))), "multiplier": float(_q(mult, Decimal("0.0001"))),
            "capped": capped, "adj": float(adj), "low": float(low), "high": float(high),
            "sheets": a["sheets"], "sheet": _show(a["sheets"]) or "—",
            "marks": a["marks"][:_KEEP_MAX], "mark_count": len(a["mark_count"]),
            "note": "; ".join(a["notes"]), "source_rows": a["rows"], "kind": "+".join(a["kinds"]),
            "est_qty": min(a["est_qty"], a["qty"]),
            "_low": low, "_high": high, "_mid": adj * a["qty"], "_uncapped": _q(base * raw, _C01) * a["qty"],
        })
    return out


def _split(sub_low: Decimal, sub_high: Decimal, heavy: bool) -> list[dict]:
    """Task split; the last row absorbs rounding so both columns sum to the subtotals exactly."""
    pcts = [_d(h if heavy else s) for _t, s, h, _n in TASK_SPLIT]

    def column(sub: Decimal) -> list[Decimal]:
        vals = [_q(sub * p / 100, _C1) for p in pcts[:-1]]
        last = sub - sum(vals, Decimal(0))
        if last >= 0:
            return vals + [last]
        # Only reachable for subtotals of a few hours: largest remainder keeps every row ≥ 0.
        tenths = int(_q(sub * 10, _ONE))
        raw = [sub * 10 * p / 100 for p in pcts]
        floors = [int(r) for r in raw]
        order = sorted(range(len(raw)), key=lambda k: raw[k] - floors[k], reverse=True)
        for k in order[: tenths - sum(floors)]:
            floors[k] += 1
        return [Decimal(f) / 10 for f in floors]

    lows, highs = column(sub_low), column(sub_high)
    return [{"task": t, "pct": float(p), "low": float(lo), "high": float(hi), "note": n + (
        " (moment / seismic share > 20 %)" if heavy and s != h else "")}
            for (t, s, h, n), p, lo, hi in zip(TASK_SPLIT, pcts, lows, highs)]


def _estimate(pieces: list[_Piece], ctx: dict) -> dict:
    region = ctx["region"]
    labels = ctx.get("labels") or {}
    lines = _price(_aggregate(pieces), ctx["factor_cap_reason"], labels, region != "USA")

    sub_low = _q(sum((ln["_low"] for ln in lines), Decimal(0)), _C1)
    sub_high = _q(sum((ln["_high"] for ln in lines), Decimal(0)), _C1)
    mid_total = sum((ln["_mid"] for ln in lines), Decimal(0))
    heavy_mid = sum((ln["_mid"] for ln in lines if {"SEISMIC", "MOMENT1"} & set(ln["factors"])), Decimal(0))
    heavy = mid_total > 0 and heavy_mid / mid_total > _HEAVY_SHARE

    comp = ctx.get("completeness") or {}
    why = ctx.get("reasons") or {}
    checklist = {k: _truthy(comp.get(k)) for k, _ in CHECKLIST}
    reasons = {k: (_txt(why.get(k)) or _NOT_CONFIRMED) for k, _ in CHECKLIST if not checklist[k]}
    met = sum(checklist.values())
    level, buf = ("Complete", 5) if met == 6 else ("Partial", 10) if met >= 4 else ("Limited", 20)

    buf_low, buf_high = _q(sub_low * buf / 100, _C1), _q(sub_high * buf / 100, _C1)
    lock_low, lock_high = _lock_hours(sub_low, buf), _lock_hours(sub_high, buf)
    currency, cost_low, cost_high = _lock_costs(region, lock_low, lock_high)
    split = _split(sub_low, sub_high, heavy)

    risks = list(ctx.get("risks") or [])
    capped = [ln for ln in lines if ln["capped"]]
    if capped:
        extra = sum((ln["_uncapped"] - ln["_mid"] for ln in capped), Decimal(0))
        risks.append({"risk": f"Combined complexity multiplier capped at ×{FACTOR_CAP:.2f} on {len(capped)} line(s); "
                              "record a cap reason in the take-off if the full product is justified",
                      "hours": float(_q(extra, _C1)), "sheet": ""})
    est_lines = [ln for ln in lines if ln["est_qty"]]
    if est_lines:
        hrs = sum((_d(ln["adj"]) * ln["est_qty"] for ln in est_lines), Decimal(0))
        risks.append({"risk": f"{len(est_lines)} estimate line(s) include quantities, sizes or lengths flagged "
                              "as estimated in the take-off — confirm through the RFIs",
                      "hours": float(_q(hrs, _C1)), "sheet": ""})
    derived = [ln for ln in lines if "derived" in ln["kind"]]
    if derived:
        hrs = sum((ln["_mid"] for ln in derived), Decimal(0))
        risks.append({"risk": "Assembly quantities derived from MTO rows (no assembly listed): "
                              + ", ".join(dict.fromkeys(ln["label"] for ln in derived)),
                      "hours": float(_q(hrs, _C1)), "sheet": ""})
    risks.sort(key=lambda r: r.get("hours") or 0.0, reverse=True)

    group_map = _group_map(ctx, lines)
    estimate = {
        "region": region, "currency": currency, "project": ctx.get("project") or {}, "sheets": ctx.get("sheets") or [],
        "rows": [{k: v for k, v in ln.items() if not k.startswith("_")} for ln in lines],
        "subtotal_low": float(sub_low), "subtotal_high": float(sub_high),
        "checklist": checklist, "reasons": reasons, "criteria_met": met, "completeness": level,
        "buffer_pct": buf, "buffer_low": float(buf_low), "buffer_high": float(buf_high),
        "lock_hrs_low": lock_low, "lock_hrs_high": lock_high, "lock_cost_low": cost_low, "lock_cost_high": cost_high,
        "split_rows": split, "heavy_share": heavy, "group_map": group_map, "risks": risks,
        "rfis": ctx.get("rfis") or [], "assumptions": ctx.get("assumptions") or [],
        "exclusions": ctx.get("exclusions") or [], "next_actions": ctx.get("next_actions") or [],
        "user_overrides": ctx.get("user_overrides") or {}, "notes": ctx.get("notes") or "",
        "quote_requested": _truthy((ctx.get("user_overrides") or {}).get("quote_requested")),
        "factor_cap_reason": ctx["factor_cap_reason"], "tonnage": ctx.get("tonnage"),
        "source": ctx.get("source") or "MTO take-off", "skipped_rows": ctx.get("skipped_rows", 0),
        "row_coverage": ctx.get("row_coverage") or {},
    }
    estimate["consistency"] = _consistency(estimate, lines)
    return estimate


def _lock_hours(subtotal: Decimal, buffer_pct: int) -> int:
    """LOCK hours = round_half_up(subtotal × (1 + buffer % / 100)) — one rounding, straight to the hour."""
    return int(_q(subtotal * (1 + _d(buffer_pct) / 100), _ONE))


def _lock_costs(region: str, lock_low: int, lock_high: int) -> tuple[str, int, int]:
    """The only place a rate is read: LOCK cost = LOCK hours × band, half-up to the 100."""
    currency, r_low, r_high = secure_rates.detailing_rates(region)
    low = int(_q(_d(lock_low) * _d(r_low) / 100, _ONE)) * 100
    high = int(_q(_d(lock_high) * _d(r_high) / 100, _ONE)) * 100
    return currency, low, high


def _group_map(ctx: dict, lines: list[dict]) -> list[dict]:
    """Every MTO group (and hardware) → its estimate lines and hours, with a coverage status."""
    rows_ctx: dict[int, dict] = ctx.get("row_ctx") or {}
    status: dict[int, str] = ctx.get("row_status") or {}
    labels = ctx.get("labels") or {}
    declared = ctx.get("mto_groups") or []
    order: list[str] = []
    info: dict[str, dict] = {}
    for g in declared:
        code = _txt(g.get("group")).upper()
        if code and code not in info:
            order.append(code)
            info[code] = {"mto_lines": _int(g.get("line_items")), "pieces": _int(g.get("pieces")), "declared": True}
    by_group: dict[str, list[int]] = {}
    for i, c in rows_ctx.items():
        code = "HARDWARE" if status.get(i) == "HARDWARE" else c["group"]
        by_group.setdefault(code, []).append(i)
    for code in by_group:
        if code in info and not info[code]["pieces"]:
            info[code]["pieces"] = sum(rows_ctx[i]["qty"] for i in by_group[code])
        if code in info and not info[code]["mto_lines"]:
            info[code]["mto_lines"] = len(by_group[code])
        if code not in info:
            order.append(code)
            info[code] = {"mto_lines": len(by_group[code]),
                          "pieces": sum(rows_ctx[i]["qty"] for i in by_group[code])}
    hw = ctx.get("hardware_summary") or []
    if hw and "HARDWARE" not in info:
        order.append("HARDWARE")
        info["HARDWARE"] = {"mto_lines": len(hw), "pieces": sum(_int(h.get("qty")) for h in hw if isinstance(h, dict))}
    rank = {g: i for i, g in enumerate(_GROUP_ORDER + ["UNGROUPED", "HARDWARE"])}
    order.sort(key=lambda g: rank.get(g, len(_GROUP_ORDER)))

    out = []
    for code in order + [g for g in dict.fromkeys(ln["group"] for ln in lines) if g not in info]:
        mine = [ln for ln in lines if ln["group"] == code]
        idxs = by_group.get(code, [])
        st = [status.get(i, "") for i in idxs]
        mto = info.get(code)
        if code == "HARDWARE":
            state, ok, note = "COUNTED ONLY", True, "counted in MTO, no detailing hours"
        elif mine:
            state, ok, note = "ESTIMATED", True, ""
        elif mto is None:
            state, ok, note = "ESTIMATED", True, ""
        elif not idxs and mto["mto_lines"] <= 0:
            state, ok, note = "NO MTO LINES", True, ""
        elif idxs and all(s in ("ASSEMBLY", "ITEMIZED") for s in st if s not in ("JOIST", "HARDWARE")) \
                and any(s in ("ASSEMBLY", "ITEMIZED") for s in st):
            state, ok, note = "IN ASSEMBLY", True, "priced through assembly lines"
        elif idxs and all(s in ("JOIST", "HARDWARE") for s in st):
            state, ok, note = "EXCLUDED", True, "joist / hardware rows only"
        else:
            state, ok, note = "NO ESTIMATE LINES", False, "MTO lines without estimate lines"
        out.append({
            "group": code, "label": labels.get(code) or GROUP_LABELS.get(code) or code.title(),
            "mto": mto is not None, "mto_lines": mto["mto_lines"] if mto else 0, "pieces": mto["pieces"] if mto else 0,
            "estimate_lines": len(mine), "items": [ln["item"] for ln in mine],
            "low": float(sum((ln["_low"] for ln in mine), Decimal(0))),
            "high": float(sum((ln["_high"] for ln in mine), Decimal(0))),
            "status": state, "passed": ok, "note": note,
        })
    return out


def _consistency(e: dict, lines: list[dict]) -> list[dict]:
    split_low = sum((_d(r["low"]) for r in e["split_rows"]), Decimal(0))
    split_high = sum((_d(r["high"]) for r in e["split_rows"]), Decimal(0))
    pct = sum((_d(r["pct"]) for r in e["split_rows"]), Decimal(0))
    sub_low, sub_high = _d(e["subtotal_low"]), _d(e["subtotal_high"])
    raw_low = sum((ln["_low"] for ln in lines), Decimal(0))
    raw_high = sum((ln["_high"] for ln in lines), Decimal(0))
    lock_ok = (e["lock_hrs_low"] == _lock_hours(sub_low, e["buffer_pct"])
               and e["lock_hrs_high"] == _lock_hours(sub_high, e["buffer_pct"]))
    cost_ok = (e["lock_cost_low"] % 100 == 0 and e["lock_cost_high"] % 100 == 0
               and e["lock_cost_low"] <= e["lock_cost_high"])
    missing = [g["label"] for g in e["group_map"] if not g["passed"]]
    mto_groups = [g for g in e["group_map"] if g["mto"]]
    over = [ln for ln in e["rows"] if ln["multiplier"] > FACTOR_CAP + 1e-9]
    cur = e["currency"]
    hrs = f"{e['lock_hrs_low']:,} / {e['lock_hrs_high']:,} hrs"
    money = f"{cur} {e['lock_cost_low']:,} / {cur} {e['lock_cost_high']:,}"
    return [
        {"check": "Sec 3 subtotal = Sec 4 subtotal = Manifest (Low)", "expected": _h1(sub_low),
         "actual": _h1(split_low), "passed": split_low == sub_low},
        {"check": "Sec 3 subtotal = Sec 4 subtotal = Manifest (High)", "expected": _h1(sub_high),
         "actual": _h1(split_high), "passed": split_high == sub_high},
        {"check": "Σ line hours = Sec 3 subtotal (Low / High)", "expected": f"{_h1(sub_low)} / {_h1(sub_high)}",
         "actual": f"{_h2(raw_low)} / {_h2(raw_high)}",
         "passed": abs(raw_low - sub_low) <= Decimal("0.05") and abs(raw_high - sub_high) <= Decimal("0.05")},
        {"check": "LOCK hours identical in Manifest, Sec 1, Sec 6, Sec 7", "expected": hrs, "actual": hrs,
         "passed": lock_ok},
        {"check": "LOCK cost identical in Manifest, Sec 1, Sec 7", "expected": money, "actual": money,
         "passed": cost_ok},
        {"check": "Task split = 100.0 %", "expected": "100.0%", "actual": f"{pct:.1f}%", "passed": pct == 100},
        {"check": "Every MTO group has estimate lines", "expected": f"{len(mto_groups)} group(s) covered",
         "actual": ("missing: " + ", ".join(missing)) if missing else f"{len(mto_groups)} group(s) covered",
         "passed": not missing},
        {"check": f"Factor cap ×{FACTOR_CAP:.2f} respected",
         "expected": f"≤ ×{FACTOR_CAP:.2f}" if not e["factor_cap_reason"] else "uncapped — reason recorded",
         "actual": f"max ×{max((ln['multiplier'] for ln in e['rows']), default=1.0):.3f}",
         "passed": bool(e["factor_cap_reason"]) or not over},
    ]


# ─── Public: estimate from the take-off ──────────────────────────────────────

def _inputs(raw, overrides: dict) -> dict:
    ei = dict(raw) if isinstance(raw, dict) else {}
    for k in ("erection_sheets", "delegated_connections", "connection_types", "factor_cap_reason"):
        if overrides.get(k) not in (None, "", [], {}):
            ei[k] = overrides[k]
    return ei


def _tonnage(totals: dict, region: str) -> dict:
    if region == "USA":
        v = (_f(totals.get("est_tons")) or _f(totals.get("gross_short_tons")) or _f(totals.get("gross_lb")) / 2000.0
             or _f(totals.get("gross_kg")) / 907.18474)
        return {"value": rhu(v, 2), "unit": "tons", "label": "tons (2,000 lb basis)"}
    v = (_f(totals.get("gross_t")) or _f(totals.get("gross_kg")) / 1000.0
         or (_f(totals.get("est_tonnage")) if _txt(totals.get("est_tonnage_unit")) == "t" else 0.0))
    return {"value": rhu(v, 2), "unit": "t", "label": "t (tonnes, 1,000 kg)"}


def _strings(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        if isinstance(v, dict):
            v = " — ".join(_txt(x) for x in v.values() if _txt(x))
        _add_unique(out, [v])
    return out


def _rfis(raw) -> list[dict]:
    out, used = [], set()
    for r in raw or []:
        if not isinstance(r, dict) or not _txt(r.get("question")):
            continue
        p = _txt(r.get("priority")).upper()
        item = {**r, "priority": p if p in ("CRITICAL", "URGENT", "STANDARD") else "STANDARD",
                "question": _txt(r.get("question")), "sheet": _txt(r.get("sheet")),
                "blocked_fields": _txt(r.get("blocked_fields")), "expected_response": _txt(r.get("expected_response")),
                "id": _txt(r.get("id"))}
        used.add(item["id"])
        out.append(item)
    n = 0
    for item in out:
        while not item["id"]:
            n += 1
            cand = f"RFI-MTO-{n:03d}"
            if cand not in used:
                item["id"] = cand
                used.add(cand)
    return out


def _takeoff_risks(t: dict, inputs: dict) -> list[dict]:
    risks: list[dict] = []
    for r in t.get("risks") or []:
        if isinstance(r, dict) and _txt(r.get("risk")):
            risks.append({"risk": _txt(r["risk"]), "hours": _f(r.get("hours_impact", r.get("hours"))),
                          "sheet": _txt(r.get("sheet"))})
        elif isinstance(r, str) and _txt(r):
            risks.append({"risk": _txt(r), "hours": 0.0, "sheet": ""})
    cov = t.get("coverage") if isinstance(t.get("coverage"), dict) else {}
    if _int(cov.get("unresolved")) > 0:
        risks.append({"risk": f"{_int(cov['unresolved'])} page(s) unresolved in the take-off — any steel on them "
                              "is not in this estimate", "hours": 0.0, "sheet": ""})
    missing = [_txt(c.get("profile")) for c in t.get("callout_crosscheck") or []
               if isinstance(c, dict) and _txt(c.get("status")).upper() == "MISSING"]
    if missing:
        risks.append({"risk": f"{len(missing)} steel callout(s) on the drawings not in the take-off: "
                              f"{_show([m for m in missing if m], limit=6)}", "hours": 0.0, "sheet": ""})
    unresolved = [m for m in t.get("metals_sweep") or []
                  if isinstance(m, dict) and _txt(m.get("resolution")).upper() == "UNRESOLVED"]
    if unresolved:
        risks.append({"risk": f"{len(unresolved)} metals reference(s) on the drawings unresolved", "hours": 0.0,
                      "sheet": ""})
    if _truthy(inputs.get("high_impact_gaps")):
        risks.append({"risk": "High-impact information gaps flagged on the drawings — see the RFI register",
                      "hours": 0.0, "sheet": ""})
    for r in _rfis(t.get("rfis"))[:3]:
        if r["priority"] == "CRITICAL":
            risks.append({"risk": f"{r['id']} (critical): {r['question']}", "hours": 0.0, "sheet": r["sheet"]})
    return risks


def from_takeoff(takeoff: dict, *, region_hint: str = "USA", user_overrides: dict | None = None) -> dict:
    """Compute the hours-based detailing estimate from a consolidated take-off dict."""
    t = takeoff if isinstance(takeoff, dict) else {}
    overrides: dict = {}
    for src in (t.get("user_overrides"), user_overrides):
        if isinstance(src, dict):
            overrides.update({k: v for k, v in src.items() if v not in (None, "", [], {})})
    inputs = _inputs(t.get("estimate_inputs"), overrides)
    region = _region(overrides.get("region")) or _region(t.get("jurisdiction")) or _region(region_hint) or "USA"
    cap_reason = _txt(inputs.get("factor_cap_reason"))

    rows = [r for r in (t.get("rows") or []) if isinstance(r, dict)]
    labels = {}
    for g in t.get("groups") or []:
        if isinstance(g, dict) and _txt(g.get("group")):
            labels[_txt(g["group"]).upper()] = _txt(g.get("label"))
    for r in rows:
        code = _txt(r.get("group")).upper()
        if code and not labels.get(code) and _txt(r.get("group_label")):
            labels[code] = _txt(r.get("group_label"))
    labels = {k: v for k, v in labels.items() if v}

    pieces, status, row_ctx, kind_of, assumptions = _map_rows(rows)
    asm_pieces, asm_assumptions, asm_risks = _assembly_pieces(t.get("assemblies"), status, row_ctx, kind_of)
    pieces += asm_pieces
    assumptions += asm_assumptions

    erect_given = _int(inputs.get("erection_sheets"))
    if pieces or erect_given > 0:
        n, found = (erect_given, []) if erect_given > 0 else _erection_sheets(t)
        if erect_given <= 0:
            assumptions.append(f"Erection drawings — assumed {n} sheet(s), one per structural plan / framing / "
                               "elevation sheet, because no erection sheet count is stated")
        pieces.append(_Piece(item="ERECTION_SHEET", group="GENERAL", qty=n, sheets=found, kind="project",
                             subs=["stated in the take-off" if erect_given > 0 else "structural plans / elevations"]))
    if _truthy(inputs.get("delegated_connections")):
        n = _int(inputs.get("connection_types"))
        pieces.append(_Piece(item="CONNECTION_DESIGN", group="GENERAL", qty=n if n > 0 else 1, kind="project",
                             subs=[f"{n} connection type(s)" if n > 0 else "connection types not stated — 1 assumed"]))

    hardware_rows = [i for i, s in status.items() if s == "HARDWARE"]
    joists = [i for i, s in status.items() if s == "JOIST"]
    exclusions = _strings(t.get("excluded_scope"))
    if hardware_rows or t.get("hardware"):
        pcs = sum(row_ctx[i]["qty"] for i in hardware_rows) or sum(
            _int(h.get("qty")) for h in t.get("hardware") or [] if isinstance(h, dict))
        exclusions.append(f"Hardware (anchor rods, bolts, studs): {pcs:,} piece(s) counted in MTO, no detailing hours.")
    deck = [d for d in t.get("deck") or [] if isinstance(d, dict)]
    if deck:
        if region == "USA":
            area = f"{sum(_f(d.get('area_ft2')) for d in deck):,.0f} ft²"
        else:
            area = f"{sum(_f(d.get('area_m2')) for d in deck):,.1f} m²"
        exclusions.append(f"Metal deck: {len(deck)} area(s), {area} — counted in MTO by area, no detailing hours.")
    if joists:
        exclusions.append(f"Open-web steel joists / joist girders: {len(joists)} take-off line(s) excluded "
                          "(supplied and detailed by the joist manufacturer).")

    rfis = _rfis(t.get("rfis"))
    project = {k: _txt(v) for k, v in (t.get("project") or {}).items() if _txt(v)} if isinstance(
        t.get("project"), dict) else {}
    sheets = []
    for s in t.get("sheets") or []:
        if isinstance(s, dict) and _txt(s.get("sheet")):
            sheets.append({"sheet": _txt(s["sheet"]), "title": _txt(s.get("title")),
                           "discipline": _txt(s.get("discipline"))})
        elif isinstance(s, str) and _txt(s):
            sheets.append({"sheet": _txt(s), "title": "", "discipline": ""})

    comp = inputs.get("completeness") if isinstance(inputs.get("completeness"), dict) else {}
    reasons = inputs.get("reasons") if isinstance(inputs.get("reasons"), dict) else {}
    if not reasons and isinstance(comp.get("reasons"), dict):
        reasons = comp["reasons"]
    next_actions = _strings(t.get("next_actions"))
    if rfis:
        crit = [r["id"] for r in rfis if r["priority"] in ("CRITICAL", "URGENT")] or [r["id"] for r in rfis]
        next_actions.insert(0, f"Issue {_show(crit, limit=4)} and update the take-off with the answers")
    for key, label in CHECKLIST:
        if not _truthy(comp.get(key)):
            next_actions.append(f"Confirm: {label.rstrip('.').lower()}")
    totals = t.get("totals") if isinstance(t.get("totals"), dict) else {}
    coverage = Counter(status.values())

    ctx = {
        "region": region, "labels": labels, "factor_cap_reason": cap_reason, "project": project, "sheets": sheets,
        "completeness": comp, "reasons": reasons, "rfis": rfis, "risks": _takeoff_risks(t, inputs) + asm_risks,
        "assumptions": _strings(t.get("assumptions")) + assumptions, "exclusions": exclusions,
        "next_actions": next_actions, "user_overrides": overrides, "notes": _txt(t.get("notes")),
        "tonnage": _tonnage(totals, region), "source": "MTO take-off",
        "mto_groups": [g for g in t.get("groups") or [] if isinstance(g, dict)],
        "row_ctx": row_ctx, "row_status": status, "hardware_summary": t.get("hardware") or [],
        "row_coverage": {
            "rows": len(rows), "priced": coverage.get("LINE", 0),
            "in_assembly": coverage.get("ASSEMBLY", 0) + coverage.get("ITEMIZED", 0),
            "hardware": coverage.get("HARDWARE", 0), "joist": coverage.get("JOIST", 0),
            "no_qty": coverage.get("NO QTY", 0), "unpriced": coverage.get("UNPRICED", 0),
            "line_items": _int(totals.get("line_items")) or len(rows), "pieces": _int(totals.get("pieces")),
        },
    }
    return _estimate(pieces, ctx)


# ─── Rendering ───────────────────────────────────────────────────────────────

def _asks_for_quote(text: str) -> bool:
    for m in _QUOTE_RE.finditer(text or ""):
        if not _NEGATION_RE.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False


def _factor_cell(r: dict) -> str:
    codes = r.get("factors") or []
    if not codes:
        return "Standard ×1.00"
    cell = " · ".join(f"{c} ×{FACTORS[c][1]:.2f}" for c in codes if c in FACTORS)
    if len(codes) > 1:
        cell += f" = ×{r.get('raw_multiplier', r.get('multiplier', 1.0)):.3f}"
    if r.get("capped"):
        cell += f" → capped ×{FACTOR_CAP:.2f}"
    return cell


def render(e: dict, *, user_text: str = "") -> str:
    cur = e.get("currency") or "USD"
    region = e.get("region") or "USA"
    proj = e.get("project") or {}
    rows = e.get("rows") or []
    money = lambda x: f"{cur} {x:,.0f}"  # noqa: E731
    quote = bool(e.get("quote_requested")) or bool((e.get("user_overrides") or {}).get("quote_requested") is True) \
        or _asks_for_quote(user_text)
    lock = (e["lock_hrs_low"], e["lock_hrs_high"])
    cost = (e["lock_cost_low"], e["lock_cost_high"])
    ton = e.get("tonnage") or {}
    parts: list[str] = []

    parts.append("## CALCULATION MANIFEST — Single Source of Truth")
    parts.append(md_table(["Parameter", "Value"], [
        ["Region", region], ["Currency", cur],
        ["Piece-Count Subtotal Hrs (Low)", f"{_h1(e['subtotal_low'])} hrs"],
        ["Piece-Count Subtotal Hrs (High)", f"{_h1(e['subtotal_high'])} hrs"],
        ["Information Completeness", e["completeness"]], ["Risk Buffer % Applied", f"{e['buffer_pct']}%"],
        ["Buffer Hrs (Low)", f"{_h1(e['buffer_low'])} hrs"], ["Buffer Hrs (High)", f"{_h1(e['buffer_high'])} hrs"],
        ["LOCK_HRS_LOW", f"**{lock[0]:,} hrs**"], ["LOCK_HRS_HIGH", f"**{lock[1]:,} hrs**"],
        ["Internal Rate", "[CONFIDENTIAL]"],
        ["LOCK_COST_LOW", f"**{money(cost[0])}**"], ["LOCK_COST_HIGH", f"**{money(cost[1])}**"],
    ], [":--", "--:"]))

    parts.append("## 1. EXECUTIVE SUMMARY")
    cov = e.get("row_coverage") or {}
    lines = [
        f"- Region: {region} · Project: {proj.get('name') or '—'}" + (f" ({proj['number']})" if proj.get("number") else ""),
        f"- Source: {e.get('source') or 'MTO take-off'}"
        + (f" — {cov.get('line_items', 0):,} line items" if cov.get("line_items") else "")
        + (f", {cov.get('pieces', 0):,} pieces" if cov.get("pieces") else ""),
        f"- Total Estimated Hours: {lock[0]:,} - {lock[1]:,} hrs",
        f"- Total Estimated Cost ({cur}): {money(cost[0])} - {money(cost[1])}",
    ]
    if ton and ton.get("value"):
        lines.append(f"- Estimated Tonnage: {float(ton['value']):,.2f} {ton.get('label') or ton.get('unit') or ''}".rstrip())
    lines.append(f"- Information Completeness: {e['completeness']} ({e['criteria_met']} of 6 criteria met) — "
                 f"risk buffer {e['buffer_pct']}%")
    if e.get("rfis"):
        crit = sum(1 for r in e["rfis"] if r.get("priority") == "CRITICAL")
        lines.append(f"- Open RFIs: {len(e['rfis'])}" + (f" ({crit} critical)" if crit else ""))
    for r in (e.get("risks") or [])[:3]:
        impact = f" (≈{r['hours']:,.0f} h affected)" if r.get("hours") else ""
        lines.append(f"- Risk: {r['risk']}{impact}")
    parts.append("\n".join(lines))

    parts.append("## 2. BASIS OF ESTIMATE")
    sheets = e.get("sheets") or []
    lines = ["**Drawings reviewed:**"]
    lines += [f"- {s['sheet']}" + (f" — {s['title']}" if s.get("title") else "") for s in sheets[:60]] or ["- —"]
    if len(sheets) > 60:
        lines.append(f"- … and {len(sheets) - 60} more sheets")
    lines += ["", f"**Source:** {e.get('source') or 'MTO take-off'} — every quantity comes from the take-off rows, "
                  "the listed assemblies and the estimate inputs; no separate count.",
              f"**Benchmarks applied:** {_BENCHMARKS.get(region, _BENCHMARKS['USA'])}",
              "**Method:** each take-off row is mapped to a detailing item by member type and piece length; "
              "Adj Hrs/Unit = Base Hrs × product of the complexity factors; Low = Adj × Qty × 0.90, "
              "High = Adj × Qty × 1.10; one line per item, factor set and group."]
    if e.get("factor_cap_reason"):
        lines.append(f"**Complexity multiplier:** uncapped — {e['factor_cap_reason']}")
    else:
        lines.append(f"**Complexity multiplier:** combined product capped at ×{FACTOR_CAP:.2f} per line.")
    if e.get("notes"):
        lines.append(f"**Take-off basis:** {e['notes']}")
    parts.append("\n".join(lines))

    parts.append("## 3. ITEMIZED PIECE-COUNT BREAKDOWN")
    body = []
    for r in rows:
        note = [r.get("sub_type") or ""]
        if r.get("marks"):
            note.append("marks " + _show(r["marks"], r.get("mark_count") or len(r["marks"])))
        note.append(r.get("note") or "")
        body.append([r.get("group_label") or "—", r["label"], f"{r['qty']:,}", r.get("unit") or "ea", _h2(r["base"]),
                     _factor_cell(r), _h2(r["adj"]), _h2(r["low"]), _h2(r["high"]), r.get("sheet") or "—",
                     "; ".join(x for x in note if x) or "—"])
    body.append(["**SUBTOTAL**", "", f"**{sum(r['qty'] for r in rows):,}**", "", "", "", "",
                 f"**{_h1(e['subtotal_low'])}**", f"**{_h1(e['subtotal_high'])}**", "", ""])
    parts.append("Piece lengths are classed in feet; base hours and multipliers are identical across regions.")
    parts.append(md_table(["Group", "Item Type", "Qty", "Unit", "Base Hrs/Unit", "Complexity Factors", "Adj Hrs/Unit",
                           "Est Hrs (Low)", "Est Hrs (High)", "Source Sheets", "Note"], body,
                          [":--", ":--", "--:", ":--", "--:", ":--", "--:", "--:", "--:", ":--", ":--"]))

    parts.append("## 4. HOURS BY TASK CATEGORY")
    trows = [[s["task"], _h1(s["low"]), _h1(s["high"]), f"{s['pct']:.1f}%", s.get("note") or "—"]
             for s in e.get("split_rows") or []]
    trows.append(["**SUBTOTAL**", f"**{_h1(e['subtotal_low'])}**", f"**{_h1(e['subtotal_high'])}**", "100.0%",
                  "Equals Section 3"])
    parts.append(md_table(["Task", "Est Hrs (Low)", "Est Hrs (High)", "% of Total", "Note"], trows,
                          [":--", "--:", "--:", "--:", ":--"]))

    parts.append("## 5. ESTIMATE BASIS & COMPLETENESS CHECK")
    lines = [f"Information Completeness: **{e['completeness']}** — criteria met: {e['criteria_met']} of 6."]
    for key, label in CHECKLIST:
        ok = e["checklist"].get(key)
        lines.append(f"- {'[x]' if ok else '[ ]'} {label}" + ("" if ok else f" — {e['reasons'].get(key, _NOT_CONFIRMED)}"))
    parts.append("\n".join(lines))
    gm = e.get("group_map") or []
    if gm:
        parts.append("**MTO group coverage:**")
        parts.append(md_table(["Group", "MTO Lines", "Pieces", "Estimate Lines", "Est Hrs (Low)", "Est Hrs (High)", "Status"], [
            [g["label"], f"{g['mto_lines']:,}" if g["mto"] else "—", f"{g['pieces']:,}" if g["mto"] else "—",
             g["estimate_lines"],
             "—" if g["group"] == "HARDWARE" else _h2(g["low"]), "—" if g["group"] == "HARDWARE" else _h2(g["high"]),
             g["status"] + (f" — {g['note']}" if g.get("note") else "")]
            for g in gm], [":--", "--:", "--:", "--:", "--:", "--:", ":--"]))

    parts.append("## 6. RISK BUFFER & ADJUSTED HOURS")
    parts.append(md_table(["Row", "Low Hrs", "High Hrs"], [
        ["Section 3 Subtotal", _h1(e["subtotal_low"]), _h1(e["subtotal_high"])],
        [f"Risk Buffer ({e['buffer_pct']}%)", f"+{_h1(e['buffer_low'])}", f"+{_h1(e['buffer_high'])}"],
        ["**ADJUSTED TOTAL HOURS**", f"**{lock[0]:,}**", f"**{lock[1]:,}**"],
    ], [":--", "--:", "--:"]))
    parts.append("Buffer tiers: Complete (6/6 criteria) 5% · Partial (4–5) 10% · Limited (3 or fewer) 20%. "
                 "Buffer hours are shown to 0.1 h; adjusted hours = subtotal × (1 + buffer %), rounded half-up to the hour.")

    parts.append(f"## 7. COST CONVERSION ({cur})")
    parts.append(md_table(["Field", "Value"], [
        ["Region", region], ["Adjusted Hours (Low)", f"{lock[0]:,} hrs"], ["Adjusted Hours (High)", f"{lock[1]:,} hrs"],
        ["Blended Hourly Rate", "[CONFIDENTIAL]"],
        [f"**Project Cost — Low ({cur})**", f"**{money(cost[0])}**"],
        [f"**Project Cost — High ({cur})**", f"**{money(cost[1])}**"],
    ], [":--", "--:"]))
    if lock[0] < 100:
        parts.append("Minimum engagement fee may apply — confirm with project lead.")

    parts.append("## 8. ASSUMPTIONS & EXCLUSIONS")
    assumptions = e.get("assumptions") or [
        "Beam end connections — assumed standard shear tab / fin plate / FEP unless a moment symbol is shown."]
    parts.append("**Key Assumptions:**\n" + "\n".join(f"- {a}" for a in assumptions))
    excl = [
        "Precast panel detailing.",
        "Stamping / professional sign-off of delegated connection calculations (PE / P.Eng / RPEQ).",
        "3D MEP coordination and clash detection.",
        "Vendor-furnished items: open-web steel joists, joist girders, joist bridging, metal deck, pre-engineered stairs.",
        "Phased / future-construction steel not in the permit set.",
    ] + list(e.get("exclusions") or [])
    parts.append("**Standard Exclusions:**\n" + "\n".join(f"- {x}" for x in dict.fromkeys(excl)))
    parts.append("**Potential Scope Creep (PM awareness):**\n- Connection or framing design changes during detailing.\n"
                 "- Owner-furnished equipment supports not yet designed.\n- Tenant fit-out steel.\n"
                 "- RFI volume above a standard allowance.")

    if quote:
        parts.append("## 9. CLIENT-FACING QUOTATION DRAFT")
        parts.append("\n\n".join([
            f"Thank you for the opportunity to price the steel detailing for {proj.get('name') or 'your project'}. "
            "Our team delivers the 3D model, shop and erection drawings with senior in-house checking.",
            f"**Estimated effort:** {lock[0]:,} - {lock[1]:,} hours.",
            f"**Fee range:** {money(cost[0])} - {money(cost[1])}.",
            "**Included:** 3D model, shop drawings, erection drawings, internal checking, RFI management.",
            "**Excluded:** " + "; ".join(x.rstrip(".") for x in excl[:4]) + ".",
            _QUOTE_STANDARD.get(region, _QUOTE_STANDARD["USA"]),
            "We would welcome a call to confirm the schedule and the start date.",
        ]))

    parts.append("## 10. FINAL RECOMMENDATION & NEXT STEPS")
    text = (f"The estimate is {e['completeness'].lower()}-information quality ({e['criteria_met']}/6 criteria) with a "
            f"{e['buffer_pct']}% risk buffer, giving {lock[0]:,}-{lock[1]:,} hours. ")
    if e.get("rfis"):
        text += f"Resolve {', '.join(r['id'] for r in e['rfis'][:4])} before committing to a fixed fee. "
    nxt = (e.get("next_actions") or [])[:3]
    if nxt:
        text += "Next actions: " + "; ".join(x.rstrip(".") for x in nxt) + ". "
    text += "Re-estimate if the steel scope changes by more than 10% or connection design is delegated after award."
    parts.append(text)

    parts.append("## CONSISTENCY CHECK")
    parts.append(md_table(["Check", "Expected", "Actual", "Result"], [
        [c["check"], c["expected"], c["actual"], "PASS" if c["passed"] else "FAIL"] for c in e.get("consistency") or []
    ], [":--", ":--", ":--", ":--"]))

    if e.get("rfis"):
        parts.append("## RFI REGISTER")
        parts.append(render_rfis(e["rfis"], region))
    return "\n\n".join(parts)


# ─── Rule card (mode-registry prompt) ────────────────────────────────────────

def detailing_system_prompt() -> str:
    """The 2C rule card: how Estimation Pro turns the take-off into detailing hours."""
    items = "\n".join(f"  {k:<18} {lbl} — {hrs:.2f} h per {unit}" for k, (lbl, hrs, unit) in BASE_HOURS.items())
    factors = "\n".join(f"  {k:<10} {lbl} ×{m:.2f}" for k, (lbl, m) in FACTORS.items())
    split = "\n".join(f"  {t:<24} {s:>4.1f} %" + (f"  ({h:.1f} % when moment / seismic lines exceed 20 % of hours)"
                                                   if s != h else "") for t, s, h, _n in TASK_SPLIT)
    checklist = "\n".join(f"  {k:<23} {lbl}" for k, lbl in CHECKLIST)
    return f"""ESTIMATION PRO — RULE CARD 2C (hours-based detailing estimate)

The detailing estimate is computed by the platform from the MTO take-off. There is no separate counting pass:
every quantity comes from the take-off rows, the take-off assemblies and the estimate inputs, so the take-off
must carry, for every row, the member code, the complexity factor codes and the Output 2 group; and in META the
assemblies and estimate_inputs described in section 9. Hours are estimated by piece count and effort, not tonnage.

1. ITEM MAPPING (member first, then type / profile fallback)
  BEAM GIRDER HEADER COLLECTOR PURLIN GIRT TRUSS MOMENT-FRAME HOIST-BEAM OVERHEAD-DOOR-SUPPORT DOOR-FRAME
  HATCH-FRAME → beam by piece length: < 30 ft simple · 30–50 ft standard · > 50 ft complex.
  COLUMN / POST → length < 6 ft short post; heavy when W / HP nominal depth ≥ 12 in (W310 / 310UC metric) or
  length > 24 ft; otherwise light.
  BRACE → vertical brace · KNEE-BRACE → knee brace · KICKER / LINTEL / SAG-ROD → lintel class.
  PLATE / EMBED / BASE-PLATE / CAP-PLATE / FALL-ANCHOR → misc plate · CLIP / SHEAR-TAB / STIFFENER /
  CONNECTION-ANGLE → misc clip · BOLLARD → bollard.
  BENT-PLATE / LEDGER / DECK-ANGLE → bent plate: Σ qty × length per factor set, units = ceil(ft ÷ 20).
  STAIR-* / HANDRAIL / GUARDRAIL / LADDER / GATE / GATE-POST / DUNNAGE / ROOF-SCREEN / CANOPY-FRAME rows carry
  no hours of their own — they are priced through the assemblies (section 9). When an assembly kind is missing
  but its rows exist, a fallback is derived from the rows and marked ASSUMED: rails Σ qty × length ÷ 2 rails;
  ladders Σ side-rail length ÷ 2; one straight stair per stringer mark; one gate leaf per gate mark × qty;
  one frame per dunnage / screen / canopy mark.
  CANOPY_FRAME is priced only when the canopy group has no itemized beam / column rows.
  ANCHOR-ROD / BOLT / STUD (hardware) and metal deck: counted in MTO, no detailing hours.
  Anything else (MISC): misc plate when the row is a plate, otherwise lintel class ("mapped by type").
  Open-web steel joists, joist girders and bridging are never estimated.
  ERECTION_SHEET: estimate_inputs.erection_sheets, else one per structural plan / framing / elevation sheet
  (minimum 1). CONNECTION_DESIGN: only when connection design is delegated — one per connection type.

2. BASE HOURS (identical in every region)
{items}

3. COMPLEXITY FACTORS (codes in the row's factors column, "+"-joined)
{factors}
  HSS is added automatically for HSS / pipe / tube profiles (not on rails and ladders, whose base hours assume
  pipe), GALV when the finish is galvanized. The combined multiplier is the product, capped at ×{FACTOR_CAP:.2f}
  unless estimate_inputs.factor_cap_reason states why the full product applies.

4. LINES
  One estimate line per (item, factor set, group); quantities summed, sheets and marks merged.
  Adj Hrs/Unit = Base × multiplier (0.01 h) · Low = Adj × Qty × 0.90 · High = Adj × Qty × 1.10.

5. INFORMATION COMPLETENESS (estimate_inputs.completeness — true / false each)
{checklist}
  6 of 6 → Complete, 5 % buffer · 4–5 → Partial, 10 % · 3 or fewer → Limited, 20 %.
  A criterion that is not stated counts as not met ("not confirmed on the drawings").

6. LOCKED VALUES
  Buffer hours = subtotal × buffer % (0.1 h). LOCK_HRS = subtotal × (1 + buffer %), rounded half-up to the hour.
  LOCK_COST = LOCK_HRS × the confidential regional band, rounded half-up to the 100. The rate is never shown;
  every rate field reads [CONFIDENTIAL]. Every section prints the same four locked values.

7. TASK SPLIT (sums to exactly 100 %; the last row absorbs rounding)
{split}

8. CONSISTENCY
  Section 3 subtotal = Section 4 subtotal = Manifest (low and high); locked hours and cost identical in the
  Manifest, Section 1 and Section 7; every MTO group has estimate lines (hardware: counted only); factor cap
  respected.

9. WHAT THE TAKE-OFF PROVIDES
  rows: member, factors, group per row (plus profile, qty, length, mark, sheet, finish, flags).
  assemblies: [{{kind, mark, sheet, qty, flights, length_ft, leaves, frames, stair_type, curved, group}}]
    kind STAIR | LADDER | GATE | HANDRAIL | GUARDRAIL | CANOPY_FRAME | DUNNAGE_FRAME | ROOF_SCREEN;
    qty = number of items; flights, length_ft, leaves and frames are per item; stair_type STRAIGHT | SWITCHBACK.
  estimate_inputs: {{erection_sheets, delegated_connections, connection_types, completeness {{...}}, reasons {{...}},
    high_impact_gaps, factor_cap_reason}}.

Report wording: "Estimated Tonnage" in tons (2,000 lb basis) for the USA and t elsewhere; no hourly rate, no
comparison with other estimates, no self-reference."""


# ─── v1 compatibility shims ──────────────────────────────────────────────────
# The v1 flow ran a separate piece-count pass whose rows named the item code directly. These
# shims price such batches with the v2 engine so callers that still hold v1 output keep working.

DETAIL_COLUMNS = ["no", "item", "sub_type", "qty", "qty_basis", "span", "factors", "sheet", "note"]
_LEGACY_ITEMS = {"LADDER_STRAIGHT": "LADDER", "LADDER_CAGED": "LADDER", "CANOPY": "CANOPY_FRAME",
                 "CONNECTION_PACKAGE": "CONNECTION_DESIGN"}


def parse_batch(text: str) -> tuple[dict, list[dict]]:
    """v1 compatibility: (META dict, piece-count rows) from one counting batch."""
    m = re.search(r"<<<META>>>(.*?)<<<END_META>>>", text or "", re.S | re.I)
    return loads_lenient(m.group(1) if m else ""), parse_rows(text, DETAIL_COLUMNS)


def consolidate(batches: list[dict], *, region_hint: str = "USA") -> dict:
    """v1 compatibility: merge piece-count batches into a v2 estimate dict. New code calls from_takeoff()."""
    batches = [b for b in batches or [] if isinstance(b, dict)]
    metas = [b.get("meta") if isinstance(b.get("meta"), dict) else {} for b in batches]
    votes = Counter(r for r in (_region(m.get("region")) for m in metas) if r)
    region = votes.most_common(1)[0][0] if votes else (_region(region_hint) or "USA")

    pieces: list[_Piece] = []
    skipped = 0
    for b in batches:
        for r in b.get("rows") or []:
            if not isinstance(r, dict) or W.is_joist(r.get("item"), r.get("sub_type"), r.get("note")):
                continue
            code = re.sub(r"[^A-Z_]", "", _txt(r.get("item")).upper().replace(" ", "_").replace("-", "_"))
            code = _LEGACY_ITEMS.get(code, code)
            if code not in BASE_HOURS:
                skipped += 1
                continue
            qty = _int(r.get("qty"))
            if qty <= 0:
                continue
            span = _txt(r.get("span"))
            pieces.append(_Piece(item=code, group="", qty=qty, factors=_ordered(_factor_codes(r.get("factors"))),
                                 sheets=[_txt(r.get("sheet"))], subs=[_txt(r.get("sub_type"))], kind="legacy",
                                 notes=[x for x in (_txt(r.get("qty_basis")), f"span {span}" if span else "",
                                                    _txt(r.get("note"))) if x]))

    completeness, reasons = {}, {}
    for key, _ in CHECKLIST:
        vals = [m["completeness"].get(key) for m in metas if isinstance(m.get("completeness"), dict)]
        vals = [v for v in vals if v is not None]
        completeness[key] = bool(vals) and all(_truthy(v) for v in vals)
        for m in metas:
            why = ((m.get("completeness") or {}).get("reasons") or {}).get(key) if isinstance(
                m.get("completeness"), dict) else None
            if why and key not in reasons:
                reasons[key] = _txt(why)

    def union(key: str) -> list[str]:
        out: list[str] = []
        for m in metas:
            out += [x for x in _strings(m.get(key)) if re.sub(r"[^a-z0-9]", "", x.lower())
                    not in {re.sub(r"[^a-z0-9]", "", y.lower()) for y in out}]
        return out

    risks, rfis, sheets, project, overrides = [], [], [], {}, {}
    seen_r, seen_q, seen_s = set(), set(), set()
    for m in metas:
        for r in m.get("risks") or []:
            if isinstance(r, dict) and _txt(r.get("risk")):
                k = re.sub(r"[^a-z0-9]", "", _txt(r["risk"]).lower())[:120]
                if k not in seen_r:
                    seen_r.add(k)
                    risks.append({"risk": _txt(r["risk"]), "hours": _f(r.get("hours_impact")), "sheet": _txt(r.get("sheet"))})
        for r in m.get("rfis") or []:
            if isinstance(r, dict) and _txt(r.get("question")):
                k = re.sub(r"[^a-z0-9]", "", _txt(r["question"]).lower())[:160]
                if k not in seen_q:
                    seen_q.add(k)
                    rfis.append({**r, "id": ""})
        for s in m.get("sheets") or []:
            if isinstance(s, dict) and _txt(s.get("sheet")) and _txt(s["sheet"]) not in seen_s:
                seen_s.add(_txt(s["sheet"]))
                sheets.append({"sheet": _txt(s["sheet"]), "title": _txt(s.get("title")),
                               "discipline": _txt(s.get("discipline"))})
        for k, v in (m.get("project") or {}).items() if isinstance(m.get("project"), dict) else ():
            if _txt(v) and not project.get(k):
                project[k] = _txt(v)
        for k, v in (m.get("user_overrides") or {}).items() if isinstance(m.get("user_overrides"), dict) else ():
            if v not in (None, "", [], {}) and k not in overrides:
                overrides[k] = v
    rfis = _rfis(sorted(rfis, key=lambda r: {"CRITICAL": 0, "URGENT": 1}.get(_txt(r.get("priority")).upper(), 2)))
    for i, r in enumerate(rfis, 1):
        r["id"] = f"RFI-{i:03d}"

    ctx = {
        "region": _region(overrides.get("region")) or region, "labels": {}, "factor_cap_reason": "",
        "project": project, "sheets": sheets, "completeness": completeness, "reasons": reasons, "rfis": rfis,
        "risks": risks, "assumptions": union("assumptions"),
        "exclusions": [x for x in union("exclusions") if "joist" not in x.lower()],
        "next_actions": union("next_actions"), "user_overrides": overrides,
        "notes": next((_txt(m.get("notes")) for m in metas if _txt(m.get("notes"))), ""),
        "tonnage": None, "source": "piece-count batches", "skipped_rows": skipped,
    }
    return _estimate(pieces, ctx)
