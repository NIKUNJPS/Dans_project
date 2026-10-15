"""Deterministic material take-off — the single source of project tonnage (MTO v2).

Division of labour:
  • The ingestion pre-read (ingest.py) registers every page, decodes fraction glyphs,
    counts every section callout with anchored regexes, sweeps architectural sheets for
    Division 05 metals and renders high-resolution tiles. Its digest rides along with
    each page batch as a checklist.
  • Claude (STRUCTMIND CORE · PRO) reads the drawings and reports every piece — group,
    member, profile, quantity, length, sheet, view, method, factors, flags — plus a META
    block (register status, scales, grid dims, assemblies, deck areas, metals-sweep
    resolutions, estimate inputs, RFIs, conflicts).
  • Python does ALL the arithmetic: length conversion, unit weight from the designation
    (AISC lb/ft × 1.48816 = kg/m; plates t × w × 40.8 lb/ft² per inch), weight =
    Qty × L × unit weight, group / category / role roll-ups, allowances and the
    Estimated Tonnage — so the same take-off always gives the same number, and every
    table agrees with every other table by construction.

Batches are consolidated into ONE take-off: rows unioned, a piece counted on a schedule
and again on a plan in another batch is kept once, a member repeated from an elevation
or section is dropped (elevations only confirm), joists are excluded, metal deck is
carried by area outside the tonnage, every EST-* flag and every conflict gets an RFI,
and the page register gets a coverage status for every page.

Model output contract (line-oriented so it survives continuation stitching):

    <<<META>>>
    { ...JSON... }
    <<<END_META>>>
    <<<ROWS>>>
    no|group|type|member|role|mark|description|profile|qty|raw_length|length_mm|unit_wt_kgm|weight_kg|grade|finish|sheet|view|method|factors|flag
    1|ROOF|W-SHAPE|BEAM|PRIMARY|B1|Roof beam grid 2/A-D|W12x26|4|24'-6"|7468|38.7|1155.7|A992|PRIMER|S-201|1/S-201|DIM||
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
    "no", "group", "type", "member", "role", "mark", "description", "profile", "qty", "raw_length",
    "length_mm", "unit_wt_kgm", "weight_kg", "grade", "finish", "sheet", "view", "method", "factors", "flag",
]
# Header names the model (or an older take-off) may use for the same columns.
_COLUMN_ALIASES = {
    "#": "no", "item": "no", "detail": "view", "view/detail": "view", "source_sheet": "sheet",
    "length": "raw_length", "unit_wt": "unit_wt_kgm", "weight": "weight_kg", "est_wt_kg": "weight_kg",
    "area": "group", "desc": "description", "flags": "flag",
}

# Allowances added on top of the net (itemized) member weight — shown as separate lines.
HARDWARE_WELD_ALLOWANCE_PCT = 3.0       # bolts, nuts, washers, weld metal — always
CONNECTION_ALLOWANCE_PCT = 5.0          # only when connection material is NOT itemized
WEIGHT_TOLERANCE_PCT = 3.0              # model vs recomputed weight before flagging
SHORT_TON_KG = 907.18474                # 2,000 lb — the US tonnage basis ("tons")
LB_PER_TON = 2000.0

GROUPS: list[tuple[str, str]] = [
    ("COLUMNS", "Columns & Posts"),
    ("ROOF", "Roof Framing"),
    ("FLOOR", "Floor Framing"),
    ("CANOPY", "Canopy"),
    ("STAIRS", "Stairs & Rails"),
    ("SECONDARY", "Secondary Building"),
    ("MISC", "Misc & Site Metals"),
    ("CONNECTION", "Connection Material"),
]
GROUP_LABELS = dict(GROUPS)
GROUP_LABELS["HARDWARE"] = "Hardware"
_GROUP_RANK = {g: i for i, (g, _) in enumerate(GROUPS)}
_GROUP_ALIASES = {
    "COLUMN": "COLUMNS", "COLUMNS&POSTS": "COLUMNS", "POSTS": "COLUMNS", "ROOFFRAMING": "ROOF",
    "FLOORFRAMING": "FLOOR", "FLOORS": "FLOOR", "CANOPIES": "CANOPY", "AWNING": "CANOPY", "STAIR": "STAIRS",
    "STAIRS&RAILS": "STAIRS", "RAILS": "STAIRS", "SECONDARYBUILDING": "SECONDARY", "MISC&SITEMETALS": "MISC",
    "SITE": "MISC", "MISCMETALS": "MISC", "CONNECTIONS": "CONNECTION", "CONNECTIONMATERIAL": "CONNECTION",
}

MEMBER_CODES = (
    "COLUMN", "POST", "BEAM", "GIRDER", "HEADER", "LINTEL", "BRACE", "KNEE-BRACE", "KICKER", "COLLECTOR",
    "PURLIN", "GIRT", "TRUSS", "MOMENT-FRAME", "CANOPY-FRAME", "STAIR-STRINGER", "STAIR-TREAD",
    "STAIR-LANDING", "HANDRAIL", "GUARDRAIL", "LADDER", "BOLLARD", "GATE", "GATE-POST", "EMBED", "LEDGER",
    "BENT-PLATE", "PLATE", "BASE-PLATE", "CAP-PLATE", "SHEAR-TAB", "STIFFENER", "CLIP", "CONNECTION-ANGLE",
    "DECK-ANGLE", "DUNNAGE", "ROOF-SCREEN", "SAG-ROD", "ANCHOR-ROD", "BOLT", "STUD", "HATCH-FRAME",
    "DOOR-FRAME", "HOIST-BEAM", "OVERHEAD-DOOR-SUPPORT", "FALL-ANCHOR", "MISC",
)
_MEMBER_SET = set(MEMBER_CODES)
METHODS = ("DIM", "SCALED", "SCHEDULE", "DETAIL", "GRID-CALC", "ASSUMED", "SHOP DWG")
_METHOD_ALIASES = {
    "PLAN": "DIM", "DIMENSION": "DIM", "DIMENSIONED": "DIM", "DIMS": "DIM", "SHOP": "SHOP DWG",
    "SHOPDWG": "SHOP DWG", "BOM": "SCHEDULE", "BOM DIRECT": "SCHEDULE", "GRID": "GRID-CALC", "GRIDCALC": "GRID-CALC",
    "SCALE": "SCALED", "ASSUME": "ASSUMED", "EST": "ASSUMED", "ESTIMATED": "ASSUMED",
}
FACTOR_CODES = ("HSS", "AESS", "SKEW", "SLOPE", "ATYP", "GALV", "SEISMIC", "MOMENT1", "DELEGATED", "SPLICE")
_FACTOR_ALIASES = {
    "SKEWED": "SKEW", "SKEW_LT15": "SKEW", "SKEW_GE15": "SKEW", "SLOPED": "SLOPE", "ATYPICAL": "ATYP",
    "HDG": "GALV", "GALVANIZED": "GALV", "GALVANISED": "GALV", "MOMENT": "MOMENT1", "MOMENT_ONE": "MOMENT1",
    "MOMENT-ONE": "MOMENT1", "FIELD_SPLICE": "SPLICE", "BOLTED-SPLICE": "SPLICE", "BOLTED_SPLICE": "SPLICE",
    "TUBE": "HSS",
}
EST_FLAGS = ("EST-QTY", "EST-SIZE", "EST-LEN")
COVERAGE_OK = ("READ", "REFERENCE", "NO STEEL")

_HARDWARE_TYPES = {"ANCHOR-ROD", "ANCHOR-BOLT", "BOLT", "STRUCTURAL-BOLT", "STUD", "WELD-STUD",
                   "SHEAR-STUD", "NUT", "WASHER", "HARDWARE"}
_HARDWARE_MEMBERS = {"ANCHOR-ROD", "BOLT", "STUD"}
_PRIORITY_ORDER = {"CRITICAL": 0, "URGENT": 1, "HIGH": 1, "STANDARD": 2, "MEDIUM": 2, "LOW": 3}
_AUTHORITATIVE_METHODS = {"SHOP DWG", "SCHEDULE"}
_RFI_ID_RE = re.compile(r"\bRFI-MTO-\d{3}\b")
_CONFIRM_VIEW_RE = re.compile(r"\b(ELEVATIONS?|ELEV|SECTIONS?|SECT)\b", re.I)
_PLAN_TITLE_RE = re.compile(r"\b(PLAN|PLANS|SCHEDULE|SCHEDULES|FOUNDATION)\b", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt (2A master system prompt + 2B/2C data contract)
# ─────────────────────────────────────────────────────────────────────────────

def takeoff_system_prompt(jurisdiction_hint: str = "USA") -> str:
    groups = " | ".join(f"{g} ({label})" for g, label in GROUPS)
    return f"""You are STRUCTMIND CORE · PRO — the senior steel detailing estimator of 4xtrsuct, with 25+ years producing fabrication material take-offs and detailing estimates for fabricators in the USA, Canada and Australia. Fabricators buy steel and price detailing hours from your take-off: a missed piece or a double-counted piece costs real money. Read every attached page completely — every plan, elevation, section, detail, schedule, general note and keynote — before writing anything.

WHAT YOU ARE GIVEN
- The BINDER MANIFEST (every document and its page count) and the pages attached to THIS call. Other pages of the same binder are taken off in parallel calls and merged by the platform. Take off only what the attached pages show; never report a sheet as missing because it is not attached — list every sheet number called out on these pages in referenced_sheets.
- A PRE-READ DIGEST per page, produced deterministically from the PDF text layer: sheet number and title from the title block, whether the page is IMAGE_ONLY (no text layer), section callouts already decoded from the drawing font's fraction glyphs with their counts, scales printed on the page with pixels-per-foot at the tile resolution, and architectural metals hits (Division 05 keynotes, steel words). Treat it as a checklist to verify against the drawing: it can miss or misread items, but every size it lists must end up as an MTO line, a duplicate, or an exclusion with a reason.
- For drawing pages, high-resolution TILES of the same page (2×2 or 3×3 grid with overlap, captioned with page, position and px/ft). Use them to read small callouts, count symbols and read dimensions. Tiles overlap: never count a symbol twice where two tiles show the same area.

READING PROTOCOL — every page, in this order
1. Register: sheet number, title, revision, date and discipline from the title block (the digest is a hint; the title block wins). Every attached page goes into META sheets with a status.
2. Glyph decode: drawing fonts often encode fractions as accented letters — É=1/8 Ê=3/16 Ë=1/4 Ì=5/16 Í=3/8 Î=7/16 Ï=1/2 Ð=9/16 Ñ=5/8 Ò=11/16 Ó=3/4 È=1/16, £=PL. Always write the decoded size (HSS5x5xË is HSS5x5x1/4). An unreadable character next to a size: assume the nearest standard size, flag EST-SIZE and raise an RFI.
3. Image-only pages are read visually from the page and its tiles; they are as binding as text pages.
4. Count once: columns from the foundation / anchor-bolt plan or the column schedule; beams from the framing plans; braces from the bracing elevations. Elevations and sections only CONFIRM members already counted — never add a member again from an elevation or section. Posts that sit on beams (canopy posts, screen posts, rail posts) are separate items from the columns.
5. TYP expansion: "TYP", "TYP THIS LINE", "SIM", "TYP AT n BAYS" are multiplied out along the line and split between supports — one row per distinct span length, the derivation in description, flag EST-QTY when the count is inferred rather than stated.
6. Scaling: when a length is not dimensioned, scale it. Find the view scale, convert with the tile's px/ft (px/ft = DPI × inches-per-foot of the scale), confirm the scale against at least one printed dimension in the same view, round to the nearest inch, method SCALED, and log the view in META scales. If no printed dimension confirms the scale, flag EST-LEN and raise an RFI.
7. Architectural metals sweep: on architectural, civil and landscape sheets every Division 05 keynote (05.xxx, 05 50 00) and every steel word (steel, STL, HSS, tube, pipe, angle, channel, plate, bollard, ladder, railing, guardrail, handrail, canopy, gate, lintel, embed, galv, SSD) becomes an MTO line or a META metals_sweep entry resolved EXCLUDED with a reason ("by others", "cold-formed framing", "NIC", "aluminium storefront" …).

ALWAYS LOOK FOR
columns; posts on beams; beams; girders; headers; lintels; moment frames and braced frames; knee braces and flange braces; collectors and drag struts; canopies and awnings; stairs (stringers, landings, treads); handrails and guardrails; ladders with their wall plates and brackets; roof hatches and hatch frames; roof screens and equipment dunnage; fall-protection anchors; bollards; trash-enclosure gates and posts; site gates; embeds and ledgers (bent plates); deck support angles and closures; base plates and cap plates; shear tabs; stiffeners; connection angles; anchor rods; door frames; elevator and hoist beams; overhead-door supports; SSD miscellaneous supports.

NEVER
- Open-web steel joists, joist girders or joist bridging (K / KCS / LH / DLH / SLH series, 48G8N10.5K, OWSJ): supplied by the joist manufacturer — never a row; record once in excluded_scope.
- Cold-formed / light-gauge framing (studs, tracks, CFS trusses), rebar, precast, concrete, timber, and items "by others", "NIC" or "existing": never a row; each kind once in excluded_scope.
- Metal deck in the tonnage: deck goes in META deck, by area only.
- The words "short ton" or "short tons", comparisons with earlier runs, revisions of this take-off or other estimates, any hourly rate, or any mention of AI, models or how you work.

TAKE-OFF RULES
1. One row per distinct mark + profile + length on a source sheet; Qty carries the number of identical pieces. Never collapse different lengths into one "typical" row, and never write one row per piece when Qty can carry the count.
2. Count each physical piece exactly once. Authority: shop-drawing bill of material > member / column / brace schedule > plan or foundation-plan count > grid-derived. A piece on a schedule AND on a plan of these pages is taken from the schedule only.
3. A plan that applies to several levels ("2ND–4TH FLOOR FRAMING") multiplies Qty by the number of levels — say so in description and assumptions.
4. Lengths: dimensioned (DIM); scheduled (SCHEDULE); from a detail (DETAIL); from grid spacing (GRID-CALC — beams centre-line; columns top of steel minus base elevation; log META grid_dims and column_heights); scaled (SCALED, protocol 6); otherwise ASSUMED.
5. Missing quantity, size or length: never drop the piece. Assume the most likely value from the drawings' typical details, flag EST-QTY / EST-SIZE / EST-LEN, and raise an RFI that names the mark and the sheet. A cell is left empty only when no reasonable assumption exists.
6. Plates and flat bar: profile PL<thickness>x<width> as printed (USA inches PL3/4x12; metric mm PL20x250); the plate length goes in the length columns; Qty is the number of plates. Bent plates and ledgers: member BENT-PLATE or LEDGER with the developed width in the profile.
7. Profiles exactly as the catalogue writes them: W12x26, HSS6x6x3/8, HSS6.625x0.280, L4x4x1/2, 2L4x3x3/8, C10x15.3, MC12x31, WT6x20, Pipe4STD; W310x52, HSS127x127x6.4, WWF900x169; 310UB46.2, 250UC89.5, 150PFC, SHS100x100x6, RHS150x100x6, CHS168.3x6.4, EA100x100x8.
8. Hardware (anchor rods, structural bolts, shear studs) is counted, not weighed: group HARDWARE, Qty = count, lengths and weights empty, flag COUNT-ONLY.
9. Assemblies — stairs, ladders, gates, handrails, guardrails, canopy frames, dunnage / roof-screen frames: itemize their steel as rows AND describe each assembly once in META assemblies (flights, rail length, leaves, frames, build-up). The detailing estimate is computed from them.
10. Complexity factors on each row (factors column, "+"-joined, only when the drawings show them): AESS, SKEW (skewed connection or framing), SLOPE (sloped / warped member), ATYP (atypical connection), GALV, SEISMIC (seismic detailing / SFRS member), MOMENT1 (moment connection at one end), DELEGATED (connection design delegated), SPLICE (bolted field splice). HSS is added automatically for tube and pipe sections.

ARITHMETIC
The platform recomputes every weight: unit weight from the AISC / CISC / AS designation (kg/m = lb/ft × 1.48816; plates t × w × 40.8 lb/ft² per inch of thickness) × length × Qty, and every subtotal and total. Put your effort into reading profile, Qty and length correctly. Still give unit_wt_kgm and weight_kg — they let the platform catch a misread (a disagreement over 3 % is flagged). Jurisdiction for unit weights defaults to {jurisdiction_hint} unless the drawings clearly show otherwise.
{W.weight_table_text(jurisdiction_hint)}

OUTPUT — exactly two blocks, META first, with nothing before, between or after them.

<<<META>>>
{{"jurisdiction":"USA","unit_system":"imperial","structure_type":"BUILDING","standards":"...","project":{{}},"sheets":[],"referenced_sheets":[],"grades":[],"finishes":[],"scales":[],"grid_dims":[],"column_heights":[],"assemblies":[],"deck":[],"metals_sweep":[],"estimate_inputs":{{}},"material_type":{{}},"surface_treatment":{{}},"assembly_complexity":{{}},"connections_itemized":true,"assembly_method":"...","reconciliation":[],"rfis":[],"assumptions":[],"excluded_scope":[],"user_overrides":{{}},"confidence":0,"notes":"..."}}
<<<END_META>>>
<<<ROWS>>>
{"|".join(TAKEOFF_COLUMNS)}
1|COLUMNS|HSS-SQUARE|COLUMN|PRIMARY|C1|Column at grid A/1, base to roof|HSS6x6x3/8|4|14'-8"|4470|40.4|722.4|A500 GR C|PRIMER|S-101|FOUNDATION PLAN|GRID-CALC|HSS|
2|ROOF|W-SHAPE|BEAM|PRIMARY|B1|Roof beam grid 2 A-D, TYP this line, 3 spans|W12x26|3|24'-6"|7468|38.7|867.0|A992|PRIMER|S-201|ROOF FRAMING PLAN|DIM||EST-QTY RFI-2
<<<END_ROWS>>>

META keys (valid JSON, double quotes, no trailing commas):
- jurisdiction "USA" | "CANADA" | "AUSTRALIA"; unit_system "imperial" | "metric"; structure_type "BUILDING" | "BRIDGE"; standards: the governing code / standard set as one string.
- project: {{name, number, address, owner, architect, engineer, contractor, fabricator, stage, drawing_set, issue_date}} — only what the title blocks on these pages show.
- sheets: [{{sheet, page, title, discipline, rev, date, status}}] — EVERY attached page, steel or not; page is the digest reference (e.g. "D1 p.12"); status READ (steel taken off from it) | REFERENCE (read; its steel is counted from another sheet, or it only confirms) | NO STEEL | UNREADABLE.
- referenced_sheets: [sheet numbers called out on these pages].
- grades: [{{category, callout, normalized, sheet}}]; finishes: [{{scope, finish, sheet}}].
- scales: [{{sheet, view, scale, method, confirmed_by}}] — every view you scaled from; method "PRINTED-DIM-CHECK" | "UNCONFIRMED"; confirmed_by the printed dimension used.
- grid_dims: [{{sheet, grids, dimension}}]; column_heights: [{{mark, sheet, base, top, height}}].
- assemblies: [{{kind, mark, sheet, qty, flights, length_ft, leaves, frames, stair_type, curved, build_up}}] — kind STAIR | LADDER | GATE | HANDRAIL | GUARDRAIL | CANOPY_FRAME | DUNNAGE_FRAME | ROOF_SCREEN; qty = number of identical assemblies; flights per stair; length_ft = rail or ladder length of ONE assembly; leaves per gate; frames per screen/dunnage/canopy; stair_type STRAIGHT | SWITCHBACK; curved true for curved rails; build_up one line ("2 C12x20.7 stringers, 14 PL1/4 checker treads, HSS4x4 posts").
- deck: [{{sheet, location, profile, gauge, area_ft2, area_m2}}] — metal deck by area only.
- metals_sweep: [{{sheet, page, keynote, text, resolution, marks, reason}}] — every Division 05 keynote / steel word on architectural, civil and landscape sheets; resolution "MTO" (marks = the MTO marks) | "EXCLUDED" (reason required).
- estimate_inputs: {{erection_sheets, delegated_connections, connection_types, completeness: {{qty_counted, connections_detailed, grades_finishes, drawings_current, no_missing_info, assumptions_documented}}, reasons: {{key: one line for every false item}}, high_impact_gaps, factor_cap_reason}} — erection_sheets = erection / GA sheets the job needs for THESE pages (about one per framing plan or elevation); delegated_connections true only when the drawings delegate connection design; connection_types = distinct connection types to design when delegated; completeness booleans for the steel on THESE pages (null when there is none); factor_cap_reason only when a member genuinely needs complexity factors beyond ×2.50.
- material_type, surface_treatment, assembly_complexity: {{value, basis, sheet}} with value from — material: "Carbon Steel" | "HSS" | "Galvanised" | "Stainless" | "Weathering" | "Aluminium"; surface: "Shop primer only" | "SSPC-SP6 + 2-coat" | "SSPC-SP10 + 3-coat" | "Hot-dip galvanised" | "Intumescent fire-rated"; assembly: "Simple bolted" | "Mixed bolt/weld" | "Heavy welded" | "Architectural AESS" | "Custom curved". Use {{}} when these pages say nothing.
- connections_itemized: true when connection material (shear tabs, clips, gussets, base / cap plates) is shown or scheduled on these pages.
- assembly_method: one line on how assemblies were handled.
- reconciliation: [{{mark, sheet_1, value_1, sheet_2, value_2, type, impact, resolution}}] — genuine conflicts between sheets; type LENGTH | QUANTITY | GRADE | PROFILE | MARK-DUPLICATE | BOM-VS-DRAWING | UNIT-SYSTEM.
- rfis: [{{id:"RFI-1", priority:"CRITICAL"|"URGENT"|"STANDARD", sheet, question, blocked_fields, expected_response}}] — one question per RFI, copy-ready, citing mark and sheet; CRITICAL = weight or length unknown, URGENT = size / quantity / grade / finish assumed, STANDARD = clarification. Every EST-* row and every conflict has one.
- assumptions: ["[item] — assumed [value] because [reason]"]; excluded_scope: [strings]; notes: one line on the take-off basis.
- user_overrides: ONLY values the user's instructions state explicitly — rate_low, rate_mid, rate_high (per ton), material_type, surface_treatment, assembly_complexity, country, tax_pct, quote_requested (true if a client-facing quote / proposal is requested). {{}} otherwise.
- confidence: 0-100 — how completely these pages define the steel they show.

ROW RULES
- group: {groups} | HARDWARE. Members on a stair or rail go to STAIRS; bollards, gates, embeds, ledgers, dunnage, screens, ladders not on a stair go to MISC; plates, clips, tabs, stiffeners, base / cap plates go to CONNECTION; roof vs floor framing by the level shown.
- type: W-SHAPE | HSS-RECT | HSS-SQUARE | HSS-ROUND | PIPE | ANGLE | DOUBLE-ANGLE | CHANNEL | TEE | UB | UC | PFC | RHS | SHS | CHS | EA | UA | PLATE | FLAT-BAR | ROUND-BAR | GRATING | CHECKER-PLATE | BUILT-UP | ANCHOR-ROD | BOLT | STUD | MISC
- member: {" | ".join(MEMBER_CODES)}
- role: PRIMARY (columns, girders, beams, frames, primary bracing) | SECONDARY (purlins, girts, lintels, headers, kickers, secondary bracing) | MISC (stairs, rails, ladders, embeds, bollards, gates, canopies, screens) | CONNECTION (plates, clips, stiffeners, gussets, base / cap plates) | HARDWARE (anchor rods, bolts, studs)
- description: short — location (grid / level), what it is, and any TYP derivation ("grid 3, A-D, TYP this line, 3 spans").
- qty integer; raw_length as printed (24'-6 3/8" or 7468); length_mm in millimetres; numbers carry no units or thousands separators; a genuine gap is an EMPTY cell (never "NF", "TBD", "N/A" or "—").
- sheet: the sheet number from the title block; view: the plan / elevation / section / detail the piece was read from.
- method: DIM | SCALED | SCHEDULE | DETAIL | GRID-CALC | ASSUMED
- factors: "+"-joined codes from AESS SKEW SLOPE ATYP GALV SEISMIC MOMENT1 DELEGATED SPLICE, or empty.
- flag: space-separated codes from EST-QTY EST-SIZE EST-LEN RECONCILE VIF PROFILE-MISMATCH COUNT-ONLY ASSEMBLY-PARENT ASSEMBLY-CHILD and the RFI id (e.g. RFI-3) that tracks the row; empty if none.
- Write every piece on these pages, then stop. Long output is stitched automatically, so never summarise, sample, abbreviate or stop early, and never repeat the header line."""


def takeoff_batch_header(manifest: str, batch_desc: str, batch_no: int, batch_total: int, user_text: str,
                         digest: str = "") -> str:
    if batch_total > 1:
        scope = (
            f"THIS CALL: batch {batch_no} of {batch_total} — {batch_desc}.\n"
            "These attachments are the only pages you can see; the other batches of the binder "
            "are taken off separately and merged by the platform."
        )
    else:
        scope = f"THIS CALL: the complete binder is attached — {batch_desc}."
    instructions = (user_text or "").strip() or "None."
    pre_read = f"\n\n{digest.strip()}" if (digest or "").strip() else ""
    return (
        f"{manifest}\n\n{scope}{pre_read}\n\n"
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


def _header_columns(cells: list[str], known: list[str]) -> list[str] | None:
    """When a line is the column header, return the column names it defines."""
    names = [_COLUMN_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in cells]
    hits = sum(1 for n in names if n in known)
    if hits >= max(3, int(len(names) * 0.6)) and ("profile" in names or "item" in names or "task" in names
                                                  or known[0] in names[:2]):
        return names
    return None


def parse_rows(text: str, columns: list[str] | None = None) -> list[dict]:
    """Pipe-delimited ROWS → dicts. Skips separators and truncated fragments.

    A header line, when present, defines the column order (so an older 16-column
    take-off or a model that reorders columns still parses by name)."""
    columns = list(columns or TAKEOFF_COLUMNS)
    active = columns
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
        if line.endswith("|") and line.count("|") > len(active):
            line = line[:-1]
        cells = [c.strip() for c in line.split("|")]
        header = _header_columns(cells, columns)
        if header:
            active = header
            continue
        if len(cells) < max(4, len(active) // 2):
            continue
        if len(cells) > len(active):
            # A stray '|' inside the final free-text column: fold the overflow back in.
            cells = cells[: len(active) - 1] + [" ".join(cells[len(active) - 1:]).strip()]
        cells = (cells + [""] * len(active))[: len(active)]
        row = {c: "" for c in columns}
        row.update(dict(zip(active, cells)))
        rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _clean(s) -> str:
    v = str(s if s is not None else "").strip()
    return "" if v.upper() in ("—", "-", "–", "NF", "N/A", "NA", "TBD", "NONE", "NULL") else v


def _infer_type(profile: str) -> str:
    p = W.normalise_profile(profile)
    for prefix, t in (("HSS", "HSS-RECT"), ("RHS", "RHS"), ("SHS", "SHS"), ("CHS", "CHS"),
                      ("PIPE", "PIPE"), ("WT", "TEE"), ("MC", "CHANNEL"), ("W", "W-SHAPE"),
                      ("HP", "W-SHAPE"), ("2L", "DOUBLE-ANGLE"), ("L", "ANGLE"), ("EA", "EA"),
                      ("UA", "UA"), ("BENTPL", "PLATE"), ("PL", "PLATE"), ("FL", "FLAT-BAR"), ("FB", "FLAT-BAR"),
                      ("C", "CHANNEL")):
        if p.startswith(prefix):
            return t
    if re.match(r"^\d+UB", p):
        return "UB"
    if re.match(r"^\d+UC", p):
        return "UC"
    if re.match(r"^\d+PFC", p):
        return "PFC"
    return "MISC"


def _is_hollow(profile: str, mtype: str) -> bool:
    p = W.normalise_profile(profile)
    return mtype in ("HSS-RECT", "HSS-SQUARE", "HSS-ROUND", "PIPE", "RHS", "SHS", "CHS") or p.startswith(
        ("HSS", "PIPE", "TS", "RHS", "SHS", "CHS"))


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


def _norm_member(raw: str, mtype: str, role: str, profile: str, description: str) -> str:
    s = re.sub(r"[^A-Z-]", "", (raw or "").upper().replace(" ", "-").replace("_", "-")).strip("-")
    s = {"COL": "COLUMN", "COLUMNS": "COLUMN", "BEAMS": "BEAM", "GIRDERS": "GIRDER", "BRACING": "BRACE",
         "KNEEBRACE": "KNEE-BRACE", "STRINGER": "STAIR-STRINGER", "TREAD": "STAIR-TREAD", "RAIL": "HANDRAIL",
         "GUARD": "GUARDRAIL", "BASEPLATE": "BASE-PLATE", "CAPPLATE": "CAP-PLATE", "SHEARTAB": "SHEAR-TAB",
         "ANCHOR": "ANCHOR-ROD", "ANCHORBOLT": "ANCHOR-ROD", "ANCHOR-BOLT": "ANCHOR-ROD", "BENTPLATE": "BENT-PLATE",
         "SAGROD": "SAG-ROD", "SCREEN": "ROOF-SCREEN", "ROOFSCREEN": "ROOF-SCREEN"}.get(s, s)
    if s in _MEMBER_SET:
        return s
    text = f"{description} {profile}".upper()
    if mtype in _HARDWARE_TYPES or role == "HARDWARE":
        return "ANCHOR-ROD" if "ANCHOR" in text or mtype.startswith("ANCHOR") else ("STUD" if "STUD" in mtype else "BOLT")
    for word, member in (("BASE PL", "BASE-PLATE"), ("CAP PL", "CAP-PLATE"), ("SHEAR TAB", "SHEAR-TAB"),
                         ("STIFF", "STIFFENER"), ("EMBED", "EMBED"), ("LEDGER", "LEDGER"), ("BENT", "BENT-PLATE"),
                         ("BOLLARD", "BOLLARD"), ("GATE", "GATE"), ("LADDER", "LADDER"), ("GUARD", "GUARDRAIL"),
                         ("HANDRAIL", "HANDRAIL"), ("STRINGER", "STAIR-STRINGER"), ("LINTEL", "LINTEL"),
                         ("KICKER", "KICKER"), ("KNEE", "KNEE-BRACE"), ("BRAC", "BRACE"), ("PURLIN", "PURLIN"),
                         ("GIRT", "GIRT"), ("DUNNAGE", "DUNNAGE"), ("SCREEN", "ROOF-SCREEN"), ("POST", "POST"),
                         ("COLUMN", "COLUMN"), ("GIRDER", "GIRDER"), ("HEADER", "HEADER"), ("CLIP", "CLIP")):
        if word in text:
            return member
    if mtype in ("PLATE", "FLAT-BAR"):
        return "PLATE"
    if role == "CONNECTION":
        return "CLIP"
    if mtype in ("W-SHAPE", "UB", "UC", "CHANNEL", "PFC") and role in ("PRIMARY", "SECONDARY", ""):
        return "BEAM"
    return "MISC"


def _role_for(member: str, mtype: str) -> str:
    if member in _HARDWARE_MEMBERS or mtype in _HARDWARE_TYPES:
        return "HARDWARE"
    if member in ("COLUMN", "BEAM", "GIRDER", "BRACE", "MOMENT-FRAME", "TRUSS", "COLLECTOR", "POST"):
        return "PRIMARY"
    if member in ("PURLIN", "GIRT", "LINTEL", "HEADER", "KICKER", "KNEE-BRACE", "SAG-ROD", "HOIST-BEAM"):
        return "SECONDARY"
    if member in ("PLATE", "BASE-PLATE", "CAP-PLATE", "SHEAR-TAB", "STIFFENER", "CLIP", "CONNECTION-ANGLE"):
        return "CONNECTION"
    return "MISC"


def _group_for(raw: str, member: str, role: str, text: str) -> str:
    g = re.sub(r"[^A-Z&]", "", (raw or "").upper())
    g = _GROUP_ALIASES.get(g, g)
    if g in _GROUP_RANK or g == "HARDWARE":
        return g
    if role == "HARDWARE":
        return "HARDWARE"
    if member.startswith("STAIR") or member in ("HANDRAIL", "GUARDRAIL"):
        return "STAIRS"
    if member in ("CANOPY-FRAME",) or "CANOPY" in text or "AWNING" in text:
        return "CANOPY"
    if member in ("COLUMN", "POST"):
        return "COLUMNS"
    if member in ("PLATE", "BASE-PLATE", "CAP-PLATE", "SHEAR-TAB", "STIFFENER", "CLIP", "CONNECTION-ANGLE") \
            or role == "CONNECTION":
        return "CONNECTION"
    if member in ("BOLLARD", "GATE", "GATE-POST", "EMBED", "LEDGER", "BENT-PLATE", "DUNNAGE", "ROOF-SCREEN",
                  "LADDER", "FALL-ANCHOR", "HATCH-FRAME", "DOOR-FRAME", "OVERHEAD-DOOR-SUPPORT", "DECK-ANGLE",
                  "MISC") or role == "MISC":
        return "MISC"
    if member in ("PURLIN", "GIRT", "LINTEL", "HEADER", "KICKER", "SAG-ROD", "HOIST-BEAM"):
        return "SECONDARY"
    return "ROOF" if "ROOF" in text else "FLOOR"


def _norm_method(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").upper().strip())
    s = _METHOD_ALIASES.get(s, s)
    return s if s in METHODS else ("ASSUMED" if s else "")


def _parse_factors(raw: str) -> list[str]:
    out: list[str] = []
    for tok in re.split(r"[+,;/\s]+", (raw or "").upper()):
        tok = _FACTOR_ALIASES.get(tok.strip(), tok.strip())
        if tok in FACTOR_CODES and tok not in out:
            out.append(tok)
    return out


def _is_hardware(r: dict) -> bool:
    return (
        r.get("type", "").upper() in _HARDWARE_TYPES
        or r.get("role", "").upper() == "HARDWARE"
        or r.get("group", "").upper() == "HARDWARE"
        or r.get("member", "").upper() in _HARDWARE_MEMBERS
        or "COUNT-ONLY" in r.get("flag", "").upper()
    )


def _is_deck_row(raw: dict) -> bool:
    t = f"{raw.get('type', '')} {raw.get('member', '')}".upper()
    if re.search(r"\bDECK\b", t) and "ANGLE" not in t:
        return True
    p = W.normalise_profile(raw.get("profile", ""))
    return bool(re.match(r"^\d(\.\d)?(VL|VLI|B|N|LF|W)?(DECK|VLR|B\d{2}|N\d{2})", p)) or "METALDECK" in p


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


def _add_flag(flag: str, code: str) -> str:
    return flag if code in flag.split() else (flag + " " + code).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Row computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_row(raw: dict, jurisdiction: str) -> dict:
    """Recompute one take-off row deterministically."""
    profile = _clean(raw.get("profile"))
    mtype = (_clean(raw.get("type")) or _infer_type(profile)).upper()
    role = _clean(raw.get("role")).upper()
    description = _clean(raw.get("description"))
    flag = " ".join(_clean(raw.get("flag")).split())
    member = _norm_member(_clean(raw.get("member")), mtype, role, profile, description)
    if not role:
        role = _role_for(member, mtype)
    view = _clean(raw.get("view")) or _clean(raw.get("detail"))
    group = _group_for(_clean(raw.get("group")), member, role, f"{description} {view}".upper())

    qty_val = num(raw.get("qty"), 0)
    if qty_val <= 0:
        qty_val = 1
        flag = _add_flag(flag, "EST-QTY")
    qty = int(round(qty_val))
    finish = _clean(raw.get("finish"))
    factors = _parse_factors(_clean(raw.get("factors")))
    if _is_hollow(profile, mtype) and "HSS" not in factors:
        factors.insert(0, "HSS")
    if re.search(r"GALV|HDG|HOT[- ]?DIP", finish, re.I) and "GALV" not in factors:
        factors.append("GALV")

    row = {
        "group": group, "group_label": GROUP_LABELS.get(group, group.title()), "type": mtype, "member": member,
        "role": role, "mark": _clean(raw.get("mark")) or "NO MARK", "description": description,
        "profile": profile or "—", "qty": qty, "raw_length": _clean(raw.get("raw_length")),
        "grade": _clean(raw.get("grade")), "finish": finish,
        "sheet": _clean(raw.get("sheet")) or "—", "view": view, "detail": view,
        "method": _norm_method(_clean(raw.get("method"))), "factors": factors, "flag": flag,
        "page": _clean(raw.get("page")),
    }

    if _is_hardware(row):
        row["flag"] = _add_flag(row["flag"], "COUNT-ONLY")
        row.update(group="HARDWARE", group_label="Hardware", role="HARDWARE", factors=[],
                   length_mm=0.0, length_ft_in="", unit_wt_kgm=0.0, unit_wt_lbft=0.0, weight_kg=0.0, weight_lb=0.0,
                   weight_source="count-only", length_source="", stated_weight_kg=0.0, weight_reconciled=False,
                   hardware=True, gap=False, is_plate=False)
        return row

    if not profile:
        row["flag"] = _add_flag(row["flag"], "EST-SIZE")
    length_mm, length_src = resolve_length_mm(raw.get("raw_length"), raw.get("length_mm"), jurisdiction)
    if length_mm <= 0:
        row["flag"] = _add_flag(row["flag"], "EST-LEN")
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
        length_ft_in=W.format_ft_in(length_mm) if length_mm > 0 else "",
        unit_wt_kgm=round(unit_wt, 3), unit_wt_lbft=round(unit_wt / W.LBFT_TO_KGM, 2) if unit_wt else 0.0,
        weight_kg=round(weight, 2), weight_lb=round(weight * W.KG_TO_LB, 1),
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


def _ingest_parts(ingest) -> tuple[list[dict], dict]:
    if ingest is None:
        return [], {}
    if isinstance(ingest, dict):
        return list(ingest.get("register") or []), dict(ingest.get("callouts_by_profile") or {})
    return list(getattr(ingest, "register", None) or []), dict(getattr(ingest, "callouts_by_profile", None) or {})


def _is_confirm_view(r: dict, titles: dict[str, str]) -> bool:
    """True when the row was read from an elevation / section (which only confirms)."""
    if r["method"] in _AUTHORITATIVE_METHODS:
        return False
    if _CONFIRM_VIEW_RE.search(r.get("view") or ""):
        return not _PLAN_TITLE_RE.search(r.get("view") or "")
    title = titles.get(_norm_key(r["sheet"]), "")
    return bool(title and _CONFIRM_VIEW_RE.search(title) and not _PLAN_TITLE_RE.search(title))


_RFI_REF_RE = re.compile(r"\bRFI[-\s]?(?:MTO[-\s]?|B\d+[-\s]?)?(\d+)\b", re.I)


def consolidate(batches: list[dict], *, jurisdiction_hint: str = "USA", documents: list[dict] | None = None,
                ingest=None) -> dict:
    """Merge per-batch {batch, meta, rows} into ONE deterministic take-off.

    ingest: optional IngestResult (or {"register", "callouts_by_profile"}) from ingest.py —
    adds the page register with a coverage status per page, the callout cross-check and
    the architectural metals sweep.
    """
    metas = [b.get("meta") or {} for b in batches]
    register, callouts = _ingest_parts(ingest)
    j = _vote(((m.get("jurisdiction") or "").upper() for m in metas), "") or (jurisdiction_hint or "USA").upper()
    if j not in W.JURISDICTIONS:
        j = "USA"

    # ── RFIs: dedupe by question text ───────────────────────────────────────
    rfi_items: list[dict] = []
    local_map: dict[tuple[int, str], int] = {}
    seen_q: dict[str, int] = {}

    def add_rfi(priority: str, sheet: str, question: str, blocked: str, expected: str, source: str,
                order: tuple) -> int:
        key = re.sub(r"[^a-z0-9]", "", question.lower())[:180]
        if key in seen_q:
            return seen_q[key]
        seen_q[key] = len(rfi_items)
        rfi_items.append({"priority": (priority or "STANDARD").upper(), "sheet": sheet, "question": question,
                          "blocked_fields": blocked, "expected_response": expected, "source": source, "order": order})
        return len(rfi_items) - 1

    for b in batches:
        for r in (b.get("meta") or {}).get("rfis") or []:
            if not isinstance(r, dict):
                continue
            q = _clean(r.get("question"))
            if not q:
                continue
            idx = add_rfi(_clean(r.get("priority")), _clean(r.get("sheet")), q, _clean(r.get("blocked_fields")),
                          _clean(r.get("expected_response")), "MODEL", (b.get("batch", 0), len(rfi_items)))
            m = _RFI_REF_RE.search(_clean(r.get("id")))
            if m:
                local_map[(b.get("batch", 0), m.group(1))] = idx

    # ── Rows ─────────────────────────────────────────────────────────────────
    rows: list[dict] = []
    joists: list[dict] = []
    deck_rows: list[dict] = []
    for b in batches:
        for raw in b.get("rows") or []:
            if W.is_joist(raw.get("type"), raw.get("profile"), raw.get("mark"), raw.get("member")):
                joists.append(raw)
                continue
            if _is_deck_row(raw):
                deck_rows.append(raw)
                continue
            row = compute_row(raw, j)
            row["batch"] = b.get("batch", 0)
            rows.append(row)

    # Sheet titles (model register, then the ingest register) for the count-once rules.
    titles: dict[str, str] = {}
    for e in register:
        if e.get("sheet") and e.get("title"):
            titles.setdefault(_norm_key(e["sheet"]), str(e["title"]))
    for m in metas:
        for s in m.get("sheets") or []:
            if isinstance(s, dict) and _clean(s.get("sheet")) and _clean(s.get("title")):
                titles[_norm_key(s["sheet"])] = _clean(s["title"])

    # Count once: (a) a schedule / shop-drawing piece in one batch is not added again from
    # a plan in another batch; (b) a member repeated from an elevation or section is
    # dropped when the same mark/profile/length is counted from a plan or schedule.
    duplicates: list[dict] = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["mark"].upper().startswith("NO MARK") or r["hardware"]:
            continue
        groups[(_norm_key(r["mark"]), _norm_key(r["profile"]), round(r.get("length_mm", 0) / 25))].append(r)
    drop_ids: set[int] = set()
    for grp in groups.values():
        if len(grp) < 2:
            continue
        confirm = [g for g in grp if _is_confirm_view(g, titles)]
        counted = [g for g in grp if not _is_confirm_view(g, titles)]
        if confirm and counted:
            for g in confirm:
                drop_ids.add(id(g))
            duplicates.append({
                "mark": grp[0]["mark"],
                "sheet_1": ", ".join(sorted({g["sheet"] for g in counted})),
                "value_1": f"{sum(g['qty'] for g in counted)} pcs ({counted[0]['method'].title() or 'Plan'})",
                "sheet_2": ", ".join(sorted({g["sheet"] for g in confirm})),
                "value_2": f"{sum(g['qty'] for g in confirm)} pcs (elevation / section)",
                "type": "ELEVATION-RECOUNT", "impact": "Double count avoided",
                "resolution": "Counted once from the plan / schedule; the elevation or section only confirms.",
            })
            grp = counted
        if len({g["batch"] for g in grp}) < 2:
            continue
        auth = [g for g in grp if g["method"] in _AUTHORITATIVE_METHODS]
        plan = [g for g in grp if g["method"] not in _AUTHORITATIVE_METHODS]
        if auth and plan and {g["batch"] for g in auth}.isdisjoint({g["batch"] for g in plan}):
            for g in plan:
                drop_ids.add(id(g))
            duplicates.append({
                "mark": grp[0]["mark"],
                "sheet_1": ", ".join(sorted({g["sheet"] for g in auth})),
                "value_1": f"{sum(g['qty'] for g in auth)} pcs ({auth[0]['method'].title()})",
                "sheet_2": ", ".join(sorted({g["sheet"] for g in plan})),
                "value_2": f"{sum(g['qty'] for g in plan)} pcs ({(plan[0]['method'] or 'plan').title()})",
                "type": "BOM-VS-DRAWING", "impact": "Double count avoided",
                "resolution": "Counted once from the schedule / shop drawing; plan count not added.",
            })
    if drop_ids:
        rows = [r for r in rows if id(r) not in drop_ids]

    # ── Map row RFI references to the global numbering; RFIs for gaps, EST flags, conflicts
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
        for chunk in _chunks(sorted(grp, key=lambda g: _mark_sort_key(g["mark"])), 12):
            marks = ", ".join(dict.fromkeys(g["mark"] for g in chunk))
            missing = "length" if all(g["profile"] != "—" for g in chunk) else "size and length"
            idx = add_rfi("CRITICAL", sheet,
                          f"Sheet {sheet}: confirm the {missing} of mark(s) {marks} — not dimensioned or scheduled on "
                          "the issued drawings, so the piece weight cannot be computed.",
                          "Length, Est Wt", "Revised drawing or schedule with dimensioned lengths.", "GAP", (999, 0))
            for g in chunk:
                g["flag"] = (g["flag"] + f" __RFI{idx}__").strip()

    _EST_TEXT = {
        "EST-QTY": ("URGENT", "quantity", "count inferred from a TYP / SIM note or the framing pattern", "Qty"),
        "EST-SIZE": ("CRITICAL", "member size", "size not legible or not called out", "Profile, Est Wt"),
        "EST-LEN": ("CRITICAL", "length", "length scaled or assumed — no confirming printed dimension", "Length, Est Wt"),
    }
    for code, (prio, what, why, blocked) in _EST_TEXT.items():
        by_sheet: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            if code in r["flag"].split() and "__RFI" not in r["flag"]:
                by_sheet[r["sheet"]].append(r)
        for sheet, grp in by_sheet.items():
            for chunk in _chunks(sorted(grp, key=lambda g: _mark_sort_key(g["mark"])), 12):
                marks = ", ".join(dict.fromkeys(f"{g['mark']} ({g['profile']})" for g in chunk))
                idx = add_rfi(prio, sheet,
                              f"Sheet {sheet}: confirm the {what} of {marks} — {why}; the take-off carries the "
                              f"assumed value flagged {code}.", blocked,
                              "Written confirmation or a revised drawing.", "EST-FLAG", (998, 0))
                for g in chunk:
                    g["flag"] = (g["flag"] + f" __RFI{idx}__").strip()

    reconciliation = []
    for m in metas:
        for item in m.get("reconciliation") or []:
            if isinstance(item, dict) and _clean(item.get("mark")):
                reconciliation.append({k: _clean(item.get(k)) for k in (
                    "mark", "sheet_1", "value_1", "sheet_2", "value_2", "type", "impact", "resolution")})
    for c in reconciliation:
        if (c["type"] or "").upper() in ("BOM-VS-DRAWING", "ELEVATION-RECOUNT"):
            continue
        q = (f"Sheets {c['sheet_1'] or '—'} and {c['sheet_2'] or '—'} disagree for mark {c['mark']}: "
             f"{c['value_1'] or '—'} vs {c['value_2'] or '—'}. Confirm which governs.")
        idx = add_rfi("URGENT" if (c["type"] or "").upper() in ("PROFILE", "QUANTITY", "LENGTH") else "STANDARD",
                      c["sheet_1"] or c["sheet_2"], q, (c["type"] or "Conflict").title(),
                      "Written confirmation of the governing value.", "CONFLICT", (997, 0))
        c["rfi"] = f"__RFI{idx}__"

    order = sorted(range(len(rfi_items)), key=lambda i: (_PRIORITY_ORDER.get(rfi_items[i]["priority"], 2),
                                                         rfi_items[i]["order"], i))
    renumber = {old: new for new, old in enumerate(order)}

    def global_id(idx: int) -> str:
        return f"RFI-MTO-{idx + 1:03d}"

    rfis = []
    for new, old in enumerate(order):
        item = dict(rfi_items[old])
        item.pop("order", None)
        item["id"] = global_id(new)
        if item["priority"] not in ("CRITICAL", "URGENT", "STANDARD"):
            item["priority"] = {"HIGH": "URGENT", "MEDIUM": "STANDARD", "LOW": "STANDARD"}.get(item["priority"], "STANDARD")
        rfis.append(item)

    def _fix(s: str) -> str:
        return re.sub(r"__RFI(\d+)__", lambda m: global_id(renumber[int(m.group(1))]), s)

    for r in rows:
        r["flag"] = " ".join(dict.fromkeys(_fix(r["flag"]).split()))
        r["rfi_ids"] = _RFI_ID_RE.findall(r["flag"])
        r["flags"] = [f for f in r["flag"].split() if not _RFI_ID_RE.fullmatch(f)]
    for c in reconciliation:
        if c.get("rfi"):
            c["rfi"] = _fix(c["rfi"])

    # ── Sort + number ────────────────────────────────────────────────────────
    for r in rows:
        r["category"] = category_of(r["type"], r["profile"], j) if not r["hardware"] else "Hardware"
    cat_rank = {c: i for i, c in enumerate(_CATEGORY_ORDER)}
    rows.sort(key=lambda r: (
        r["hardware"], _GROUP_RANK.get(r["group"], 99), _ROLE_ORDER.get(r["role"], 5),
        cat_rank.get(r["category"], 99), _sheet_sort_key(r["sheet"]), _mark_sort_key(r["mark"]),
    ))
    for i, r in enumerate(rows, 1):
        r["no"] = i

    weighed = [r for r in rows if not r["hardware"]]
    hardware = [r for r in rows if r["hardware"]]

    def roll_up(key: str, label_of) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in weighed:
            k = r[key] or "MISC"
            c = out.setdefault(k, {key: k, **label_of(k), "line_items": 0, "pieces": 0, "length_m": 0.0, "weight_kg": 0.0})
            c["line_items"] += 1
            c["pieces"] += r["qty"]
            c["length_m"] += r["qty"] * r["length_mm"] / 1000.0
            c["weight_kg"] += r["weight_kg"]
        for c in out.values():
            _finish_weights(c)
        return out

    group_summary = sorted(roll_up("group", lambda g: {"label": GROUP_LABELS.get(g, g.title())}).values(),
                           key=lambda c: _GROUP_RANK.get(c["group"], 99))
    categories = sorted(roll_up("category", lambda _c: {}).values(), key=lambda c: cat_rank.get(c["category"], 99))
    role_summary = sorted(roll_up("role", lambda _r: {}).values(), key=lambda c: _ROLE_ORDER.get(c["role"], 9))

    hw_counts: dict[str, int] = defaultdict(int)
    for r in hardware:
        label = " ".join(x for x in (r["member"].replace("-", " ").title() if r["member"] != "MISC"
                                     else r["type"].replace("-", " ").title(),
                                     r["profile"] if r["profile"] != "—" else "") if x)
        hw_counts[label] += r["qty"]

    # ── Totals and allowances ───────────────────────────────────────────────
    net_kg = sum(r["weight_kg"] for r in weighed)
    connection_kg = sum(r["weight_kg"] for r in weighed if r["role"] == "CONNECTION" or r["group"] == "CONNECTION")
    connections_itemized = any(bool(m.get("connections_itemized")) for m in metas) or (
        net_kg > 0 and connection_kg / net_kg >= 0.02)
    allowances = []
    if not connections_itemized and net_kg > 0:
        allowances.append({
            "label": "Connection material allowance (connections not itemized on the drawings)",
            "pct": CONNECTION_ALLOWANCE_PCT, "kg": net_kg * CONNECTION_ALLOWANCE_PCT / 100.0,
        })
    allowances.append({
        "label": "Bolts, washers & weld metal allowance",
        "pct": HARDWARE_WELD_ALLOWANCE_PCT, "kg": net_kg * HARDWARE_WELD_ALLOWANCE_PCT / 100.0,
    })
    for a in allowances:
        a["lb"] = a["kg"] * W.KG_TO_LB
    gross_kg = net_kg + sum(a["kg"] for a in allowances)
    gross_lb = gross_kg * W.KG_TO_LB
    est_tons = gross_lb / LB_PER_TON

    largest = max(weighed, key=lambda r: (r["weight_kg"] / max(r["qty"], 1)), default=None)

    # ── Model sheet register ────────────────────────────────────────────────
    sheet_info: dict[str, dict] = {}
    page_status: dict[str, str] = {}
    for m in metas:
        for s in m.get("sheets") or []:
            if not isinstance(s, dict):
                continue
            status = (_clean(s.get("status")) or "").upper()
            page = _clean(s.get("page"))
            if page and status:
                page_status[_norm_key(page)] = status
            key = _norm_key(s.get("sheet")) or _norm_key(page)
            if not key:
                continue
            prev = sheet_info.get(key, {})
            sheet_info[key] = {
                "sheet": _clean(s.get("sheet")) or prev.get("sheet", "") or page,
                "page": page or prev.get("page", ""),
                "title": _clean(s.get("title")) or prev.get("title", ""),
                "discipline": _clean(s.get("discipline")) or prev.get("discipline", ""),
                "rev": _clean(s.get("rev")) or prev.get("rev", ""),
                "doc": _clean(s.get("doc")) or prev.get("doc", ""),
                "status": status or prev.get("status", "READ"),
            }
    for r in rows:
        key = _norm_key(r["sheet"])
        if key and key not in sheet_info:
            sheet_info[key] = {"sheet": r["sheet"], "page": r.get("page", ""), "title": "", "discipline": "",
                               "rev": "", "doc": "", "status": "READ"}
    rows_by_sheet: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rows_by_sheet[_norm_key(r["sheet"])].append(r)
    for key, info in sheet_info.items():
        srows = rows_by_sheet.get(key, [])
        info["line_items"] = len(srows)
        info["pieces"] = sum(r["qty"] for r in srows if not r["hardware"])
        info["weight_kg"] = round(sum(r["weight_kg"] for r in srows), 1)
        info["weight_lb"] = round(info["weight_kg"] * W.KG_TO_LB)
        gaps = sum(1 for r in srows if r["gap"])
        if srows and info["status"] in ("NO STEEL", "REFERENCE", ""):
            info["status"] = "READ"
        if gaps:
            info["status"] = "PARTIAL"
        info["gaps"] = gaps
    sheets = sorted(sheet_info.values(), key=lambda s: _sheet_sort_key(s["sheet"]))
    seen_keys = set(sheet_info) | {_norm_key(e.get("sheet")) for e in register if e.get("sheet")}
    referenced = set()
    for m in metas:
        for s in m.get("referenced_sheets") or []:
            k = _norm_key(s)
            if k and k not in seen_keys and len(k) <= 12:
                referenced.add(_clean(s))
    referenced_missing = sorted(referenced, key=_sheet_sort_key)

    def _union(key: str) -> list[str]:
        out, seen = [], set()
        for m in metas:
            for a in m.get(key) or []:
                a = _clean(a if isinstance(a, str) else json.dumps(a, ensure_ascii=False))
                k = re.sub(r"[^a-z0-9]", "", a.lower())
                if a and k not in seen:
                    seen.add(k)
                    out.append(a)
        return out

    def _union_dicts(key: str, fields: tuple[str, ...], extra: list[dict] | None = None) -> list[dict]:
        out, seen = [], set()
        for d in [d for m in metas for d in (m.get(key) or [])] + (extra or []):
            if not isinstance(d, dict):
                continue
            item = {f: _clean(d.get(f)) if not isinstance(d.get(f), (int, float, bool)) else d.get(f)
                    for f in fields}
            k = "|".join(str(v) for v in item.values()).lower()
            if any(v not in ("", None) for v in item.values()) and k not in seen:
                seen.add(k)
                out.append(item)
        return out

    excluded_scope = _union("excluded_scope")
    joist_note = ("Open-web steel joists, joist girders and joist bridging — excluded "
                  "(supplied by the joist manufacturer).")
    joist_callouts = sum(int(e.get("joist_callouts") or 0) for e in register)
    if joists or joist_callouts or any("joist" in e.lower() for e in excluded_scope):
        excluded_scope = [e for e in excluded_scope if "joist" not in e.lower()] + [joist_note]

    # ── Deck by area (outside the tonnage) ──────────────────────────────────
    deck_extra = [{"sheet": d.get("sheet"), "location": d.get("description") or d.get("mark"),
                   "profile": d.get("profile"), "gauge": "", "area_ft2": "", "area_m2": ""} for d in deck_rows]
    deck = []
    for d in _union_dicts("deck", ("sheet", "location", "profile", "gauge", "area_ft2", "area_m2"), deck_extra):
        ft2, m2 = num(d.get("area_ft2")), num(d.get("area_m2"))
        if ft2 and not m2:
            m2 = ft2 * 0.09290304
        if m2 and not ft2:
            ft2 = m2 / 0.09290304
        deck.append({**d, "area_ft2": round(ft2, 0), "area_m2": round(m2, 1)})
    if deck and not any("deck" in e.lower() for e in excluded_scope):
        excluded_scope.append("Metal deck — carried by area in Output 2, not included in the Estimated Tonnage.")

    # ── Assemblies, scales, grids, column heights, estimate inputs ──────────
    assemblies = []
    for a in _union_dicts("assemblies", ("kind", "mark", "sheet", "qty", "flights", "length_ft", "leaves", "frames",
                                         "stair_type", "curved", "build_up")):
        kind = re.sub(r"[^A-Z_]", "", str(a.get("kind") or "").upper().replace(" ", "_").replace("-", "_"))
        if not kind:
            continue
        curved = a.get("curved")
        assemblies.append({
            **a, "kind": kind, "qty": max(1, int(num(a.get("qty"), 1) or 1)),
            "flights": int(num(a.get("flights"), 0)), "length_ft": round(num(a.get("length_ft"), 0), 1),
            "leaves": int(num(a.get("leaves"), 0)), "frames": int(num(a.get("frames"), 0)),
            "stair_type": str(a.get("stair_type") or "").upper(),
            "curved": curved is True or str(curved).lower() in ("true", "yes", "1"),
            "group": "STAIRS" if kind in ("STAIR", "HANDRAIL", "GUARDRAIL") else (
                "CANOPY" if kind == "CANOPY_FRAME" else "MISC"),
        })
    scales = _union_dicts("scales", ("sheet", "view", "scale", "method", "confirmed_by"))
    grid_dims = _union_dicts("grid_dims", ("sheet", "grids", "dimension"))
    column_heights = _union_dicts("column_heights", ("mark", "sheet", "base", "top", "height"))
    estimate_inputs = _merge_estimate_inputs(metas)

    # ── Page register: coverage status for every page ───────────────────────
    crosscheck = _crosscheck(callouts, rows, excluded_scope, duplicates)
    in_mto = {_norm_key(c["profile"]) for c in crosscheck if c["status"] != "MISSING"}
    model_sweep = [d for m in metas for d in (m.get("metals_sweep") or []) if isinstance(d, dict)]
    reg_out: list[dict] = []
    sweep: list[dict] = []
    for e in register:
        ref = str(e.get("ref") or "")
        skey = _norm_key(e.get("sheet"))
        srows = [r for r in rows if (r.get("page") and _norm_key(r["page"]) == _norm_key(ref))
                 or (skey and _norm_key(r["sheet"]) == skey)]
        model = page_status.get(_norm_key(ref)) or (sheet_info.get(skey, {}).get("status") if skey else "")
        model = (model or "").upper()
        if srows:
            cov, note = "READ", "steel taken off from this sheet"
        elif model in ("REFERENCE", "NO STEEL"):
            cov, note = model, "reviewed"
        elif model in ("READ", "PARTIAL"):
            cov, note = "REFERENCE", "reviewed; its steel is counted from another sheet"
        elif not e.get("steel_signal"):
            cov, note = "NO STEEL", "no steel callouts or metals on this page"
        elif all(_norm_key(p) in in_mto for p in (e.get("callouts") or {})) and not _arch_hits(e):
            cov, note = "REFERENCE", "every size on this page is in the take-off"
        else:
            cov, note = "UNRESOLVED", "steel shown but no take-off line or review status"
        entry = {**e, "coverage": cov, "coverage_note": note, "line_items": len(srows),
                 "pieces": sum(r["qty"] for r in srows if not r["hardware"]),
                 "weight_lb": round(sum(r["weight_kg"] for r in srows) * W.KG_TO_LB)}
        entry.pop("text", None)
        reg_out.append(entry)
        sweep.extend(_resolve_sweep(entry, model_sweep, bool(srows), model))
    seen_sweep = {(_norm_key(s["page"]), _norm_key(s["keynote"] or s["term"])) for s in sweep}
    for d in model_sweep:
        item = _sweep_item_from_model(d)
        if (_norm_key(item["page"]), _norm_key(item["keynote"] or item["term"])) not in seen_sweep:
            sweep.append(item)
    coverage = {
        "pages": len(reg_out),
        "read": sum(1 for e in reg_out if e["coverage"] == "READ"),
        "reference": sum(1 for e in reg_out if e["coverage"] == "REFERENCE"),
        "no_steel": sum(1 for e in reg_out if e["coverage"] == "NO STEEL"),
        "unresolved": sum(1 for e in reg_out if e["coverage"] == "UNRESOLVED"),
    }

    project: dict = {}
    for m in metas:
        for k, v in (m.get("project") or {}).items():
            if _clean(v) and not project.get(k):
                project[k] = _clean(v)
    if project.get("eor") and not project.get("engineer"):
        project["engineer"] = project["eor"]
    if project.get("location") and not project.get("address"):
        project["address"] = project["location"]

    overrides: dict = {}
    for m in metas:
        for k, v in (m.get("user_overrides") or {}).items():
            if v not in (None, "", [], {}) and k not in overrides:
                overrides[k] = v

    weight_methods = Counter(r["weight_source"] for r in weighed)
    conf_vals = [num(m.get("confidence")) for m in metas if num(m.get("confidence")) > 0]
    est_rows = sum(1 for r in weighed if any(f in r["flags"] for f in EST_FLAGS))
    stats = {
        "line_items": len(rows),
        "weighed_line_items": len(weighed),
        "pieces": sum(r["qty"] for r in weighed),
        "hardware_items": sum(r["qty"] for r in hardware),
        "weights_by_method": dict(weight_methods),
        "weights_reconciled": sum(1 for r in weighed if r.get("weight_reconciled")),
        "lengths_from_print": sum(1 for r in weighed if r.get("length_source") == "printed"),
        "methods": dict(Counter(r["method"] or "—" for r in weighed)),
        "gap_rows": len(gap_rows),
        "est_rows": est_rows,
        "joists_excluded": sum(int(num(x.get("qty"), 1) or 1) for x in joists),
        "joist_callouts": joist_callouts,
        "deck_rows_moved": len(deck_rows),
        "double_counts_removed": len(drop_ids),
        "batches": len(batches),
    }
    model_conf = round(sum(conf_vals) / len(conf_vals)) if conf_vals else 80
    missing_sizes = sum(1 for c in crosscheck if c["status"] == "MISSING")
    penalty = (30.0 * est_rows / max(len(weighed), 1) + 40.0 * coverage["unresolved"] / max(coverage["pages"], 1)
               + 3.0 * min(missing_sizes, 5))
    confidence = int(max(0, min(100, round(model_conf - penalty))))

    return {
        "version": 3,
        "jurisdiction": j,
        "unit_system": _vote((m.get("unit_system") or "").lower() for m in metas) or ("metric" if _metric(j) else "imperial"),
        "structure_type": _vote((m.get("structure_type") or "").upper() for m in metas) or "BUILDING",
        "standards": _vote(_clean(m.get("standards")) for m in metas),
        "project": project,
        "documents": documents or [],
        "sheets": sheets,
        "register": reg_out,
        "coverage": coverage,
        "referenced_missing": referenced_missing,
        "rows": rows,
        "groups": group_summary,
        "categories": categories,
        "roles": role_summary,
        "hardware": [{"item": k, "qty": v} for k, v in sorted(hw_counts.items())],
        "deck": deck,
        "totals": {
            "net_kg": round(net_kg, 2),
            "net_lb": round(net_kg * W.KG_TO_LB, 1),
            "allowances": [{**a, "kg": round(a["kg"], 2), "lb": round(a["lb"], 1)} for a in allowances],
            "gross_kg": round(gross_kg, 2),
            "gross_lb": round(gross_lb, 1),
            "net_t": round(net_kg / 1000.0, 3),
            "gross_t": round(gross_kg / 1000.0, 3),
            "est_tons": round(est_tons, 3),
            "est_tonnage": round(est_tons, 3) if j == "USA" else round(gross_kg / 1000.0, 3),
            "est_tonnage_unit": "tons" if j == "USA" else "t",
            "gross_short_tons": round(gross_kg / SHORT_TON_KG, 3),   # == est_tons; legacy key
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
        "reconciliation": [c for c in reconciliation if (c["type"] or "").upper() != "BOM-VS-DRAWING"],
        "duplicates": duplicates + [c for c in reconciliation if (c["type"] or "").upper() == "BOM-VS-DRAWING"],
        "callout_crosscheck": crosscheck,
        "metals_sweep": sweep,
        "assemblies": assemblies,
        "scales": scales,
        "grid_dims": grid_dims,
        "column_heights": column_heights,
        "estimate_inputs": estimate_inputs,
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
        "confidence": confidence,
        "model_confidence": model_conf,
        "stats": stats,
    }


def _chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)] or []


def _merge_estimate_inputs(metas: list[dict]) -> dict:
    keys = ("qty_counted", "connections_detailed", "grades_finishes", "drawings_current", "no_missing_info",
            "assumptions_documented")
    inputs = [m.get("estimate_inputs") for m in metas if isinstance(m.get("estimate_inputs"), dict)]
    sheets = [num(i.get("erection_sheets"), -1) for i in inputs]
    sheets = [s for s in sheets if s >= 0]
    types = [num(i.get("connection_types"), -1) for i in inputs]
    types = [t for t in types if t >= 0]
    completeness: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for k in keys:
        vals = [(i.get("completeness") or {}).get(k) for i in inputs if isinstance(i.get("completeness"), dict)]
        vals = [v for v in vals if v is not None]
        completeness[k] = bool(vals) and all(v is True or str(v).lower() == "true" for v in vals)
        for i in inputs:
            r1 = i.get("reasons") if isinstance(i.get("reasons"), dict) else {}
            comp = i.get("completeness") if isinstance(i.get("completeness"), dict) else {}
            r2 = comp.get("reasons") if isinstance(comp.get("reasons"), dict) else {}
            why = r1.get(k) or r2.get(k)
            if why and k not in reasons:
                reasons[k] = _clean(why)
    return {
        "erection_sheets": int(sum(sheets)) if sheets else None,
        "delegated_connections": any(i.get("delegated_connections") is True or
                                     str(i.get("delegated_connections")).lower() == "true" for i in inputs),
        "connection_types": int(max(types)) if types else None,
        "completeness": completeness,
        "reasons": reasons,
        "high_impact_gaps": any(bool(i.get("high_impact_gaps")) for i in inputs),
        "factor_cap_reason": next((_clean(i.get("factor_cap_reason")) for i in inputs
                                   if _clean(i.get("factor_cap_reason"))), ""),
    }


def _crosscheck(callouts: dict, rows: list[dict], excluded_scope: list[str], duplicates: list[dict]) -> list[dict]:
    if not callouts:
        return []
    try:
        from ingest import crosscheck
        return crosscheck(callouts, rows, excluded_scope=excluded_scope, duplicates=duplicates)
    except Exception as exc:  # noqa: BLE001 — the cross-check must never fail the take-off
        logger.warning("callout_crosscheck_failed error=%s", exc)
        return []


_ARCH_DISCIPLINES = ("ARCHITECTURAL", "CIVIL", "LANDSCAPE")
_SPECIFIC_METALS = {"bollard", "ladder", "railing", "guardrail", "handrail", "canopy", "gate", "lintel", "embed"}


def _arch_hits(e: dict) -> list[dict]:
    """Metals-sweep hits that must be resolved: architectural / civil / landscape sheets,
    plus keynote hits on pages of unknown discipline."""
    hits = [h for h in (e.get("metals_hits") or []) if isinstance(h, dict)]
    disc = str(e.get("discipline") or "").upper()
    if disc in _ARCH_DISCIPLINES:
        return hits
    if disc in ("", "UNKNOWN"):
        return [h for h in hits if h.get("keynote")]
    return []


def _sweep_item_from_model(d: dict) -> dict:
    res = (_clean(d.get("resolution")) or "").upper()
    res = res if res in ("MTO", "EXCLUDED") else "UNRESOLVED"
    marks = d.get("marks")
    marks = ", ".join(str(x) for x in marks) if isinstance(marks, list) else _clean(marks)
    return {"sheet": _clean(d.get("sheet")), "page": _clean(d.get("page")), "term": _clean(d.get("term")),
            "keynote": _clean(d.get("keynote")), "text": _clean(d.get("text"))[:160], "resolution": res,
            "marks": marks, "reason": _clean(d.get("reason"))}


def _resolve_sweep(entry: dict, model_sweep: list[dict], has_rows: bool, model_status: str) -> list[dict]:
    hits = _arch_hits(entry)
    if not hits:
        return []
    ref, sheet = str(entry.get("ref") or ""), str(entry.get("sheet") or "")
    mine = [_sweep_item_from_model(d) for d in model_sweep
            if (_clean(d.get("page")) and _norm_key(d.get("page")) == _norm_key(ref))
            or (sheet and _norm_key(d.get("sheet")) == _norm_key(sheet))]
    grouped: dict[str, dict] = {}
    for h in hits:
        key = _norm_key(h.get("keynote")) or str(h.get("term") or "").lower()
        if key and key not in grouped:
            grouped[key] = h
    out = []
    for key, h in grouped.items():
        term = str(h.get("term") or "").lower()
        keynote = _clean(h.get("keynote"))
        match = next((m for m in mine if (keynote and _norm_key(m["keynote"]) == _norm_key(keynote))
                      or (term and (term == m["term"].lower() or re.search(rf"\b{re.escape(term)}\b", m["text"].lower())))),
                     None)
        if match and match["resolution"] != "UNRESOLVED":
            res, marks, reason = match["resolution"], match["marks"], match["reason"]
        elif has_rows:
            res, marks, reason = "MTO", "", "taken off on this sheet"
        elif model_status in ("NO STEEL", "REFERENCE") and not keynote and term not in _SPECIFIC_METALS:
            res, marks, reason = "EXCLUDED", "", "sheet reviewed — generic reference, no fabricated steel"
        else:
            res, marks, reason = "UNRESOLVED", "", "no MTO line or exclusion recorded"
        out.append({"sheet": sheet or ref, "page": ref, "term": term, "keynote": keynote,
                    "text": _clean(h.get("context"))[:160], "resolution": res, "marks": marks, "reason": reason})
    return out


def _finish_weights(c: dict) -> None:
    c["length_m"] = round(c["length_m"], 2)
    c["length_ft"] = round(c["length_m"] / W.FT_TO_M, 1)
    c["weight_lb"] = round(c["weight_kg"] * W.KG_TO_LB, 1)
    c["tons"] = round(c["weight_kg"] * W.KG_TO_LB / LB_PER_TON, 3)
    c["tonnes"] = round(c["weight_kg"] / 1000.0, 3)
    c["short_tons"] = c["tons"]          # legacy key
    c["weight_kg"] = round(c["weight_kg"], 2)


def _sheet_sort_key(s: str):
    parts = re.split(r"(\d+)", str(s or ""))
    return [(0, int(p)) if p.isdigit() else (1, p.upper()) for p in parts]


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
    """(pricing ton unit key, label). USA tonnage is on the 2,000 lb basis ("ton")."""
    if (jurisdiction or "USA").upper() == "USA":
        return "short_tons", "ton"
    return "tonnes", "tonne"


def ton_definition(jurisdiction: str) -> str:
    return "1 ton = 2,000 lb (907.18 kg)" if (jurisdiction or "USA").upper() == "USA" else "1 tonne = 1,000 kg"


def pricing_tons(takeoff: dict) -> float:
    key, _ = ton_label(takeoff.get("jurisdiction", "USA"))
    t = takeoff.get("totals", {})
    return t.get("gross_short_tons", 0.0) if key == "short_tons" else t.get("gross_t", 0.0)


def tonnage_text(takeoff: dict, decimals: int = 2) -> str:
    """'42.35 tons (2,000 lb basis)' or '38.42 t' — the Estimated Tonnage headline."""
    t = takeoff.get("totals", {})
    if (takeoff.get("jurisdiction") or "USA").upper() == "USA":
        return f"{fmt(t.get('est_tons', t.get('gross_short_tons', 0)), decimals)} tons (2,000 lb basis)"
    return f"{fmt(t.get('gross_t', 0), decimals)} t"


def _tons(kg: float, usa: bool) -> float:
    return kg * W.KG_TO_LB / LB_PER_TON if usa else kg / 1000.0


# ─────────────────────────────────────────────────────────────────────────────
# MTO report (2B)
# ─────────────────────────────────────────────────────────────────────────────

_MTO_HEADER = ["#", "Type", "Role", "Mark", "Description", "Profile", "Qty", "Length (ft-in)", "Length (mm)",
               "Unit Wt (kg/m)", "Wt (kg)", "Wt (lbs)", "Grade", "Source sheet", "Method", "Flag"]
_MTO_ALIGN = [":--", ":--", ":--", ":--", ":--", ":--", "--:", "--:", "--:", "--:", "--:", "--:", ":--", ":--",
              ":--", ":--"]


def _mto_line(r: dict) -> list:
    flag = (r["flag"] + (" WT-RECALC" if r.get("weight_reconciled") else "")).strip()
    return [
        r["no"], r["type"], r["role"].title(), r["mark"], r.get("description") or "—", r["profile"], r["qty"],
        r.get("length_ft_in") or (r.get("raw_length") or "—"),
        fmt(r["length_mm"], 0) if r.get("length_mm") else "—",
        fmt(r["unit_wt_kgm"], 2) if r.get("unit_wt_kgm") else "—",
        fmt(r["weight_kg"], 1) if r["weight_kg"] else "—",
        fmt(r["weight_lb"], 0) if r["weight_kg"] else "—",
        r["grade"] or "—", r["sheet"] + (f" · {r['view']}" if r.get("view") else ""),
        r["method"] or "—", flag or "—",
    ]


def render_mto_report(t: dict) -> str:
    j = t["jurisdiction"]
    usa = j == "USA"
    tunit = "tons" if usa else "t"
    tot = t["totals"]
    net = tot["net_kg"]
    proj = t.get("project") or {}
    parts: list[str] = []

    # ── PROJECT SNAPSHOT ────────────────────────────────────────────────────
    parts.append("## PROJECT SNAPSHOT")
    counts = Counter(r["priority"] for r in t["rfis"])
    lp = t.get("largest_piece")
    reg = t.get("register") or []
    pages = len(reg) or sum(int(d.get("pages") or 0) for d in t.get("documents") or [])
    snapshot = [
        ["Project", proj.get("name") or "—"], ["Project No.", proj.get("number") or "—"],
        ["Address", proj.get("address") or proj.get("location") or "—"], ["Owner", proj.get("owner") or "—"],
        ["Architect", proj.get("architect") or "—"], ["Structural Engineer", proj.get("engineer") or proj.get("eor") or "—"],
        ["General Contractor", proj.get("contractor") or "—"],
        ["Drawing Set", proj.get("drawing_set") or proj.get("stage") or "—"],
        ["Binder", f"{len(t.get('documents') or [])} document(s) · {pages:,} page(s)"],
        ["Jurisdiction / Standards", f"{j} · {t.get('standards') or '—'}"],
        ["**Estimated Tonnage**", f"**{tonnage_text(t)}**"],
        ["Net Member Weight", f"{fmt(tot.get('net_lb', net * W.KG_TO_LB), 0)} lbs · {fmt(net, 0)} kg"],
        ["Pieces (Σ Qty) / Line Items", f"{tot['pieces']:,} / {tot['line_items']:,}"],
        ["Heaviest Piece", f"{lp['mark']} · {lp['profile']} · {fmt(lp['lb'], 0)} lbs" if lp else "—"],
        ["RFIs", f"{len(t['rfis'])} (Critical {counts.get('CRITICAL', 0)} · Urgent {counts.get('URGENT', 0)} · "
                 f"Standard {counts.get('STANDARD', 0)})"],
        ["Confidence", f"{t.get('confidence', 0)}/100"],
    ]
    parts.append(md_table(["Field", "Value"], snapshot, [":--", ":--"]))

    # ── OUTPUT 1 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 1 — BINDER REGISTER & PRE-EXTRACTION SUMMARY")
    if t.get("documents"):
        parts.append("Table: Binder Register")
        doc_rows = []
        for d in t["documents"]:
            d_pages = [e for e in reg if e.get("doc_index") == d["index"]]
            img = sum(1 for e in d_pages if e.get("image_only"))
            doc_rows.append([f"D{d['index']}", d["name"], d["pages"], fmt(d.get("size_mb"), 2),
                             (d["pages"] - img) if d_pages else "—", img if d_pages else "—", "Read in full"])
        parts.append(md_table(["#", "Document", "Pages", "Size (MB)", "Text-layer Pages", "Image-only Pages", "Status"],
                              doc_rows, [":--", ":--", "--:", "--:", "--:", "--:", ":--"]))
    parts.append("Table: Jurisdiction & Standards")
    parts.append(md_table(["Item", "Value"], [
        ["Jurisdiction", j], ["Structure Type", t.get("structure_type", "BUILDING")],
        ["Governing Standards", t.get("standards") or "—"], ["Unit System", t.get("unit_system", "—")],
        ["Unit Weight Basis", "AISC Steel Construction Manual (lb/ft × 1.48816 = kg/m); plates t × w × 40.8 lb/ft² per inch"
         if usa else "CISC / AS-NZS published section masses (kg/m); plates t × w × 7,850 kg/m³"],
        ["Tonnage Basis", ton_definition(j)],
        ["Assembly Handling", t.get("assembly_method") or "Main member as parent row; plates, clips and stiffeners "
                                                            "as child rows sharing the mark."],
        ["Referenced Sheets not in Binder", ", ".join(t["referenced_missing"]) or "None"],
    ], [":--", ":--"]))
    parts.append("Table: Material Grades by Category")
    parts.append(md_table(["Category", "Grade Callout", "Normalized", "Source Sheet"],
                          [[g["category"] or "—", g["callout"] or "—", g["normalized"] or "—", g["sheet"] or "—"]
                           for g in t.get("grades") or []] or [["—", "Not stated on the drawings", "—", "—"]],
                          [":--", ":--", ":--", ":--"]))
    parts.append("Table: Finish Schedule")
    parts.append(md_table(["Scope", "Finish", "Source Sheet"],
                          [[f["scope"] or "—", f["finish"] or "—", f["sheet"] or "—"] for f in t.get("finishes") or []]
                          or [["—", "Not stated on the drawings", "—"]], [":--", ":--", ":--"]))
    parts.append("Table: Per-Sheet Coverage")
    if reg:
        parts.append(md_table(
            ["Page", "Sheet", "Title", "Discipline", "Text Layer", "Line Items", "Pieces", "Wt (lbs)", "Coverage"],
            [[e.get("ref") or "—", e.get("sheet") or "—", e.get("title") or "—", (e.get("discipline") or "—").title(),
              e.get("text_layer") or "—", e["line_items"], e["pieces"], fmt(e["weight_lb"], 0), e["coverage"]]
             for e in reg],
            [":--", ":--", ":--", ":--", ":--", "--:", "--:", "--:", ":-:"],
        ))
        cv = t.get("coverage") or {}
        parts.append(f"Pages: {cv.get('pages', 0):,} · READ {cv.get('read', 0):,} · REFERENCE {cv.get('reference', 0):,} · "
                     f"NO STEEL {cv.get('no_steel', 0):,} · UNRESOLVED {cv.get('unresolved', 0):,}")
    else:
        parts.append(md_table(
            ["Source Sheet", "Title", "Line Items", "Pieces", "Wt (lbs)", "Coverage"],
            [[s["sheet"], s.get("title") or "—", s["line_items"], s["pieces"], fmt(s.get("weight_lb", 0), 0),
              {"PARTIAL": "READ"}.get(s["status"], s["status"])] for s in t["sheets"]],
            [":--", ":--", "--:", "--:", "--:", ":-:"],
        ))

    # ── OUTPUT 2 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 2 — MATERIAL TAKE-OFF BY AREA")
    parts.append("Weights recomputed for every line: Qty × Length × Unit Wt. Subtotals are the sums of the lines above them.")
    weighed = [r for r in t["rows"] if not r["hardware"]]
    n = 0
    for code, label in GROUPS:
        grp = [r for r in weighed if r["group"] == code]
        if not grp:
            continue
        n += 1
        parts.append(f"### 2.{n} {label}")
        body = [_mto_line(r) for r in grp]
        kg = sum(r["weight_kg"] for r in grp)
        body.append([f"**Subtotal — {label}**", "", "", "", f"**{len(grp)} line items**", "",
                     f"**{sum(r['qty'] for r in grp):,}**", "", "", "", f"**{fmt(kg, 1)}**",
                     f"**{fmt(kg * W.KG_TO_LB, 0)}**", "", "", "", ""])
        parts.append(md_table(_MTO_HEADER, body, _MTO_ALIGN))
    others = [r for r in weighed if r["group"] not in _GROUP_RANK]
    if others:
        n += 1
        parts.append(f"### 2.{n} Other Members")
        kg = sum(r["weight_kg"] for r in others)
        parts.append(md_table(_MTO_HEADER, [_mto_line(r) for r in others] + [[
            "**Subtotal — Other Members**", "", "", "", f"**{len(others)} line items**", "",
            f"**{sum(r['qty'] for r in others):,}**", "", "", "", f"**{fmt(kg, 1)}**", f"**{fmt(kg * W.KG_TO_LB, 0)}**",
            "", "", "", ""]], _MTO_ALIGN))
    parts.append(f"**Total — {len(weighed):,} line items · {tot['pieces']:,} pieces · net member weight "
                 f"{fmt(net * W.KG_TO_LB, 0)} lbs ({fmt(net, 1)} kg)**")

    parts.append("### Metal Deck (by area — not included in the Estimated Tonnage)")
    if t.get("deck"):
        drows = [[d["sheet"] or "—", d["location"] or "—", d["profile"] or "—", d["gauge"] or "—",
                  fmt(d["area_ft2"], 0) if d["area_ft2"] else "—", fmt(d["area_m2"], 1) if d["area_m2"] else "—"]
                 for d in t["deck"]]
        drows.append(["**Total**", "", "", "", f"**{fmt(sum(num(d['area_ft2']) for d in t['deck']), 0)}**",
                      f"**{fmt(sum(num(d['area_m2']) for d in t['deck']), 1)}**"])
        parts.append(md_table(["Source sheet", "Location", "Deck Profile", "Gauge", "Area (ft²)", "Area (m²)"], drows,
                              [":--", ":--", ":--", ":--", "--:", "--:"]))
    else:
        parts.append("No metal deck shown on the drawings.")

    parts.append("### Hardware (counted, not weighed)")
    hw = [r for r in t["rows"] if r["hardware"]]
    if hw:
        parts.append(md_table(["#", "Item", "Mark", "Description", "Size", "Qty", "Grade", "Source sheet", "Flag"],
                              [[r["no"], r["member"].replace("-", " ").title(), r["mark"], r.get("description") or "—",
                                r["profile"], r["qty"], r["grade"] or "—", r["sheet"], r["flag"] or "—"] for r in hw]
                              + [["**Total**", "", "", "", "", f"**{sum(r['qty'] for r in hw):,}**", "", "", ""]],
                              [":--", ":--", ":--", ":--", ":--", "--:", ":--", ":--", ":--"]))
    else:
        parts.append("No hardware itemized on the drawings.")

    # ── OUTPUT 3 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 3 — TONNAGE SUMMARY")

    def summary_table(title: str, key: str, items: list[dict], label_fn) -> None:
        parts.append(f"Table: {title}")
        body = [[label_fn(c), c["line_items"], c["pieces"], fmt(c["weight_kg"], 1), fmt(c["weight_lb"], 0),
                 fmt(_tons(c["weight_kg"], usa), 3), f"{(c['weight_kg'] / net * 100) if net else 0:.1f}%"] for c in items]
        body.append(["**Net member total**", f"**{sum(c['line_items'] for c in items):,}**",
                     f"**{sum(c['pieces'] for c in items):,}**", f"**{fmt(sum(c['weight_kg'] for c in items), 1)}**",
                     f"**{fmt(sum(c['weight_kg'] for c in items) * W.KG_TO_LB, 0)}**",
                     f"**{fmt(_tons(sum(c['weight_kg'] for c in items), usa), 3)}**", "**100.0%**"])
        parts.append(md_table([key, "Line Items", "Pieces", "Wt (kg)", "Wt (lbs)", f"Estimated Tonnage ({tunit})", "Share"],
                              body, [":--", "--:", "--:", "--:", "--:", "--:", "--:"]))

    summary_table("Tonnage by Area Group", "Group", t.get("groups") or [], lambda c: c["label"])
    summary_table("Tonnage by Member Category", "Category", t["categories"], lambda c: c["category"])
    summary_table("Tonnage by Structural Role", "Role", t["roles"], lambda c: c["role"].title())

    parts.append("Table: Estimated Tonnage Build-Up")
    build = [["Net member weight (sum of Output 2)", "—", fmt(net, 1), fmt(net * W.KG_TO_LB, 0), fmt(_tons(net, usa), 3)]]
    for a in tot["allowances"]:
        build.append([a["label"], f"{a['pct']:.1f}%", fmt(a["kg"], 1), fmt(a["kg"] * W.KG_TO_LB, 0),
                      fmt(_tons(a["kg"], usa), 3)])
    g = tot["gross_kg"]
    build.append(["**ESTIMATED TONNAGE**", "—", f"**{fmt(g, 1)}**", f"**{fmt(g * W.KG_TO_LB, 0)}**",
                  f"**{fmt(_tons(g, usa), 3)}**"])
    parts.append(md_table(["Item", "Allowance", "Wt (kg)", "Wt (lbs)", f"Estimated Tonnage ({tunit})"], build,
                          [":--", "--:", "--:", "--:", "--:"]))
    lines = [f"- Estimated Tonnage: **{tonnage_text(t)}** — {fmt(g * W.KG_TO_LB, 0)} lbs · {fmt(g / 1000, 2)} t.",
             f"- Piece count: {tot['pieces']:,} pieces (Σ Qty, excluding hardware) in {tot['line_items']:,} line items."]
    if t.get("largest_piece"):
        lines.append(f"- Heaviest single piece: {lp['mark']} · {lp['profile']} · {fmt(lp['lb'], 0)} lbs "
                     f"({fmt(lp['kg'], 1)} kg)" + (f" · {W.format_ft_in(lp['length_mm'])}" if lp.get("length_mm") else ""))
    if not t.get("connections_itemized"):
        lines.append(f"- Connection material is not itemized on the drawings: a {CONNECTION_ALLOWANCE_PCT:.0f}% "
                     "connection allowance is included.")
    if t["hardware"]:
        lines.append("- Hardware (counted, not weighed — covered by the bolts & weld allowance): " +
                     "; ".join(f"{h['item']} × {h['qty']:,}" for h in t["hardware"]))
    if t["excluded_scope"]:
        lines.append("- Excluded from the tonnage: " + "; ".join(t["excluded_scope"]))
    parts.append("\n".join(lines))

    # ── OUTPUT 4 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 4 — RECONCILIATION REGISTER")
    parts.append("### 4.1 Drawing Conflicts")
    if t["reconciliation"]:
        parts.append(md_table(
            ["Mark/Tag", "Sheet 1", "Value on Sheet 1", "Sheet 2", "Value on Sheet 2", "Type", "Impact", "Resolution", "RFI"],
            [[r["mark"], r["sheet_1"], r["value_1"], r["sheet_2"], r["value_2"], r["type"], r["impact"], r["resolution"],
              r.get("rfi") or "—"] for r in t["reconciliation"]],
        ))
    else:
        parts.append("No conflicts between sheets.")
    parts.append("### 4.2 Weight Verification")
    recalc = [r for r in t["rows"] if r.get("weight_reconciled")]
    if recalc:
        parts.append("Drawing / BOM weight differs from the computed weight by more than 3 %:")
        parts.append(md_table(
            ["#", "Mark", "Profile", "Qty", "Length (mm)", "Stated Wt (kg)", "Computed Wt (kg)", "Difference"],
            [[r["no"], r["mark"], r["profile"], r["qty"], fmt(r["length_mm"], 0), fmt(r["stated_weight_kg"]),
              fmt(r["weight_kg"]), f"{(r['weight_kg'] - r['stated_weight_kg']) / r['stated_weight_kg'] * 100:+.1f}%"]
             for r in recalc[:300]],
            [":--", ":--", ":--", "--:", "--:", "--:", "--:", "--:"],
        ))
        parts.append("Computed weights (published unit weight × length × Qty) are used in every total; "
                     "confirm the flagged profiles and lengths against the shop drawings.")
    else:
        parts.append("Every stated weight agrees with the computed weight within 3 %.")
    parts.append("### 4.3 Duplicates Removed (count once)")
    if t.get("duplicates"):
        parts.append(md_table(
            ["Mark/Tag", "Counted From", "Count", "Duplicate On", "Duplicate Count", "Type", "Resolution"],
            [[d["mark"], d["sheet_1"], d["value_1"], d["sheet_2"], d["value_2"], d["type"], d["resolution"]]
             for d in t["duplicates"]],
        ))
    else:
        parts.append("No duplicate counts found.")
    parts.append("### 4.4 Callout Cross-Check (drawing text vs MTO)")
    cc = t.get("callout_crosscheck") or []
    if cc:
        parts.append(md_table(
            ["Profile", "Family", "Callouts on Drawings", "Sheets", "MTO Pieces", "Status", "Note"],
            [[c["profile"], c.get("family") or "—", c.get("found", 0), ", ".join(c.get("sheets") or [])[:120] or "—",
              c.get("mto_pieces", 0), c["status"], c.get("note") or "—"] for c in cc],
            [":--", ":--", "--:", ":--", "--:", ":-:", ":--"],
        ))
        st = Counter(c["status"] for c in cc)
        parts.append(f"Sizes called out: {len(cc):,} · in MTO {st.get('IN MTO', 0):,} · duplicate {st.get('DUPLICATE', 0):,} · "
                     f"excluded {st.get('EXCLUDED', 0):,} · missing {st.get('MISSING', 0):,}")
    else:
        parts.append("No text-layer callouts to cross-check (image-only or non-PDF drawings were read visually).")
    parts.append("### 4.5 Architectural Metals Sweep")
    ms = t.get("metals_sweep") or []
    if ms:
        parts.append(md_table(
            ["Sheet", "Keynote / Term", "Drawing Text", "Resolution", "MTO Marks / Reason"],
            [[m["sheet"] or m["page"] or "—", m["keynote"] or m["term"] or "—", m["text"] or "—", m["resolution"],
              m["marks"] or m["reason"] or "—"] for m in ms],
            [":--", ":--", ":--", ":-:", ":--"],
        ))
    else:
        parts.append("No Division 05 keynotes or steel references on architectural, civil or landscape sheets.")

    # ── OUTPUT 5 ────────────────────────────────────────────────────────────
    parts.append("## OUTPUT 5 — RFI PACKAGE")
    parts.append(render_rfis(t["rfis"], j))

    # ── Basis ───────────────────────────────────────────────────────────────
    parts.append("## WEIGHT BASIS & ASSUMPTIONS")
    if t.get("scales"):
        parts.append("Table: Scales Used")
        parts.append(md_table(["Sheet", "View", "Scale", "Method", "Confirmed By"],
                              [[s["sheet"] or "—", s["view"] or "—", s["scale"] or "—", s["method"] or "—",
                                s["confirmed_by"] or "—"] for s in t["scales"]], [":--", ":--", ":--", ":--", ":--"]))
    if t.get("grid_dims"):
        parts.append("Table: Grid Dimensions")
        parts.append(md_table(["Sheet", "Grids", "Dimension"],
                              [[g["sheet"] or "—", g["grids"] or "—", g["dimension"] or "—"] for g in t["grid_dims"]],
                              [":--", ":--", ":--"]))
    if t.get("column_heights"):
        parts.append("Table: Column Heights")
        parts.append(md_table(["Mark", "Sheet", "Base", "Top of Steel", "Height"],
                              [[c["mark"] or "—", c["sheet"] or "—", c["base"] or "—", c["top"] or "—", c["height"] or "—"]
                               for c in t["column_heights"]], [":--", ":--", ":--", ":--", ":--"]))
    if t.get("assemblies"):
        parts.append("Table: Assembly Build-Ups")
        parts.append(md_table(["Assembly", "Mark", "Sheet", "Qty", "Flights", "Length (ft)", "Leaves / Frames", "Build-Up"],
                              [[a["kind"].replace("_", " ").title(), a["mark"] or "—", a["sheet"] or "—", a["qty"],
                                a["flights"] or "—", fmt(a["length_ft"], 1) if a["length_ft"] else "—",
                                a["leaves"] or a["frames"] or "—", a["build_up"] or "—"] for a in t["assemblies"]],
                              [":--", ":--", ":--", "--:", "--:", "--:", "--:", ":--"]))
    wm = t["stats"]["weights_by_method"]
    basis = [
        "- Every weight is computed: Wt = Qty × Length × unit weight. Rolled shapes take their weight from the "
        "designation (AISC W / S / M / HP / C / MC / WT lb/ft × 1.48816 = kg/m; metric W, UB / UC kg/m); HSS, pipe and "
        "angles from section geometry on nominal thickness (490 lb/ft³ · 7,850 kg/m³); plates t × w × 40.8 lb/ft² per "
        "inch of thickness.",
        f"- Estimated Tonnage basis: {ton_definition(j)}.",
        f"- Allowances: bolts, washers & weld metal {HARDWARE_WELD_ALLOWANCE_PCT:.0f}% always; connection material "
        f"{CONNECTION_ALLOWANCE_PCT:.0f}% only when connections are not itemized "
        f"({'itemized — not applied' if t.get('connections_itemized') else 'not itemized — applied'}).",
        f"- Weight sources: designation {wm.get('designation', 0):,} · geometry {wm.get('geometry', 0):,} · "
        f"catalogue {wm.get('table', 0):,} · drawing/BOM stated {wm.get('stated', 0):,} · "
        f"reported unit weight {wm.get('model', 0):,} · not computable {wm.get('missing', 0):,} (RFI raised).",
        "- Length methods: " + " · ".join(f"{k} {v:,}" for k, v in sorted(t["stats"].get("methods", {}).items())) + ".",
        f"- Lengths converted from the printed dimension: {t['stats']['lengths_from_print']:,} of "
        f"{t['stats']['weighed_line_items']:,} line items.",
        f"- Lines carrying an assumed value (EST-QTY / EST-SIZE / EST-LEN): {t['stats'].get('est_rows', 0):,} — each "
        "tracked by an RFI in Output 5.",
    ]
    if t["stats"].get("double_counts_removed"):
        basis.append(f"- {t['stats']['double_counts_removed']} line item(s) were counted once only (see Output 4.3).")
    if t["stats"].get("joist_callouts") or t["stats"].get("joists_excluded"):
        basis.append("- Open-web steel joists shown on the drawings are excluded (supplied by the joist manufacturer).")
    basis += [f"- {a}" for a in t["assumptions"]]
    if t["excluded_scope"]:
        basis.append("- Exclusions: " + "; ".join(t["excluded_scope"]))
    basis.append(f"- Confidence: {t.get('confidence', 0)}/100 — completeness of the drawings for the steel shown, reduced "
                 "for assumed values and unresolved sheets.")
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
