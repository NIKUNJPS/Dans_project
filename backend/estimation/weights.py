"""Structural-steel unit weights + length parsing — the deterministic core of every tonnage.

The take-off model reads the drawings and reports each piece's profile, quantity and
length. EVERY weight is then recomputed here, in Python, so project tonnage is an exact
calculation (the same take-off always yields the same number) rather than an LLM's
arithmetic.

How a unit weight (kg/m) is resolved, most exact first:

  1. DESIGNATION   — the section name carries its own weight:
                       imperial W / M / S / HP / WT / MT / ST / C / MC   → lb/ft after the "x"
                       metric   W / WWF / WRF / HP / C / MC / WT (CISC)  → kg/m after the "x"
                       Australian UB / UC / WB / WC                       → kg/m after the code
  2. GEOMETRY      — closed-form section area × steel density, using NOMINAL wall thickness
                     (the basis of AISC/ASTM A500 tabulated weights):
                       rectangular / square HSS, RHS, SHS, TS   A = 2t(B+H) − 4t² − 3(4−π)t²
                       round HSS / CHS / pipe                   A = π(D − t)t
                       angles (single or double)                A = t(a + b − t) + root fillet
                       plate / flat bar                         A = t × w
                       round bar / square bar                   A = πd²/4  /  d²
                     Checked against AISC: HSS8x8x1/2 → 48.85 lb/ft, L4x4x1/2 → 12.8 lb/ft,
                     Pipe 4 Std → 10.8 lb/ft — all within ±1 %.
  3. TABLE         — Australian PFC channels (no weight in the name).
  4. MODEL         — anything else (grating, deck, proprietary) keeps the model's stated
                     kg/m or weight and is flagged by the caller.

Open-web steel joists, joist girders and joist bridging are supplied by the joist
manufacturer, not the fabricator — `is_joist()` lets every take-off exclude them.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

STEEL_DENSITY_KG_M3 = 7850.0
KG_TO_LB = 2.20462
LBFT_TO_KGM = 1.488164          # 1 lb/ft = 1.488164 kg/m
FT_TO_M = 0.3048
IN_TO_MM = 25.4
# Steel at 490 lb/ft³: one square inch of section, one foot long, weighs 3.4028 lb.
LB_PER_FT_PER_IN2 = 490.0 / 144.0
# One mm² of section, one metre long, weighs 0.00785 kg.
KG_PER_M_PER_MM2 = STEEL_DENSITY_KG_M3 / 1_000_000.0

JURISDICTIONS = ("USA", "CANADA", "AUSTRALIA")

# Australian parallel-flange channels — the designation carries depth only (OneSteel).
_AUS_PFC_KGM = {
    75: 5.92, 100: 8.33, 125: 11.9, 150: 17.7, 180: 20.9,
    200: 22.9, 230: 25.1, 250: 35.5, 300: 40.1, 380: 55.2,
}

# ASME B36.10 nominal pipe sizes: NPS → (OD in, STD wall, XS wall, XXS wall) in inches.
_PIPE_NPS = {
    0.5: (0.840, 0.109, 0.147, 0.294), 0.75: (1.050, 0.113, 0.154, 0.308),
    1.0: (1.315, 0.133, 0.179, 0.358), 1.25: (1.660, 0.140, 0.191, 0.382),
    1.5: (1.900, 0.145, 0.200, 0.400), 2.0: (2.375, 0.154, 0.218, 0.436),
    2.5: (2.875, 0.203, 0.276, 0.552), 3.0: (3.500, 0.216, 0.300, 0.600),
    3.5: (4.000, 0.226, 0.318, None), 4.0: (4.500, 0.237, 0.337, 0.674),
    5.0: (5.563, 0.258, 0.375, 0.750), 6.0: (6.625, 0.280, 0.432, 0.864),
    8.0: (8.625, 0.322, 0.500, 0.875), 10.0: (10.750, 0.365, 0.500, 1.000),
    12.0: (12.750, 0.375, 0.500, 1.000),
}


@dataclass(frozen=True)
class UnitWeight:
    kg_per_m: float
    method: str          # "designation" | "geometry" | "table"
    basis: str           # human-readable derivation, e.g. "26 lb/ft from designation"
    is_plate: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Number / profile normalisation
# ─────────────────────────────────────────────────────────────────────────────

_FRACTION_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)-)?(\d+)/(\d+)$")
# Grade text sometimes rides along with the profile ("W12x26 A992", "SHS 100x100x6 C350L0").
_GRADE_SUFFIX_RE = re.compile(
    r"\s(?:ASTM\s*)?(?:A\d{2,4}(?:\s*GR\.?\s*[A-Z0-9]+)?|GR(?:ADE)?\.?\s*\d+[A-Z]*|\d{3}W[T]?|"
    r"C[34]50L0|300PLUS|G\d{3})\b.*$"
)


def _num(token: str) -> float | None:
    """Parse '1/2', '1-1/4', '.500', '0.5', '12' → float. None if not numeric."""
    t = (token or "").strip().rstrip('"').strip()
    if not t:
        return None
    m = _FRACTION_RE.match(t)
    if m:
        whole = float(m.group(1)) if m.group(1) else 0.0
        den = float(m.group(3))
        return whole + float(m.group(2)) / den if den else None
    try:
        return float(t)
    except ValueError:
        return None


def normalise_profile(profile: str) -> str:
    """Canonical upper-case, space-free designation used for every lookup.

    'W 12 x 26', 'w12X26' and 'W12×26 (A992)' → 'W12X26';
    'HSS 5 1/2 x 5 1/2 x 3/8' → 'HSS5-1/2X5-1/2X3/8'.
    """
    if not profile:
        return ""
    p = str(profile).upper().strip()
    p = re.sub(r"\(.*?\)", " ", p)                     # drop parenthetical grade/notes
    p = _GRADE_SUFFIX_RE.sub(" ", p)                   # drop trailing grade callouts
    p = p.replace("×", "X").replace("*", "X").replace("″", '"').replace("”", '"')
    p = re.sub(r"(\d)\s+(\d+\s*/\s*\d+)", r"\1-\2", p)   # mixed number '5 1/2' → '5-1/2'
    p = re.sub(r"\s*/\s*", "/", p)
    p = re.sub(r"\s*X\s*", "X", p)
    p = p.replace('"', "").replace("'", "")
    p = re.sub(r"\s+", "", p)
    p = p.rstrip(".,;")
    return p


def _dims(rest: str) -> list[float] | None:
    parts = [x for x in rest.split("X") if x != ""]
    vals = [_num(x) for x in parts]
    if not vals or any(v is None or v <= 0 for v in vals):
        return None
    return vals  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# Section calculators
# ─────────────────────────────────────────────────────────────────────────────

def _rect_hollow_area(b: float, h: float, t: float) -> float:
    """Area of a rectangular hollow section with corner radii ro = 2t, ri = t."""
    return 2 * t * (b + h) - 4 * t * t - 3 * (4 - math.pi) * t * t


def _round_hollow_area(d: float, t: float) -> float:
    return math.pi * (d - t) * t


def _angle_area(a: float, b: float, t: float) -> float:
    """Legs a × b, thickness t, plus the root fillet (radius ≈ t)."""
    return t * (a + b - t) + (1 - math.pi / 4) * t * t


def _imperial_area_to_kgm(area_in2: float) -> float:
    return area_in2 * LB_PER_FT_PER_IN2 * LBFT_TO_KGM


def _metric_area_to_kgm(area_mm2: float) -> float:
    return area_mm2 * KG_PER_M_PER_MM2


_ROLLED_RE = re.compile(r"^(WWF|WRF|HP|WT|MT|ST|MC|W|M|S|C)(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$")
_AUS_ROLLED_RE = re.compile(r"^(\d+)(UBP|UB|UC|WB|WC)(\d+(?:\.\d+)?)$")
_AUS_PFC_RE = re.compile(r"^(\d+)PFC$")
_HOLLOW_RE = re.compile(r"^(HSS|RHS|SHS|TS|TUBE|CHS)(.+)$")
_ANGLE_RE = re.compile(r"^(\d)?(L|EA|UA|ANGLE)(.+)$")
_PLATE_RE = re.compile(r"^(PL|PLATE|FL|FB|FLAT|FLATBAR|BAR|BPL|STIFFPL|CAPPL)(.+)$")
_ROUND_BAR_RE = re.compile(r"^(RB|ROD|RD|ROUNDBAR|Ø|DIA|SR)(.+)$")
_SQUARE_BAR_RE = re.compile(r"^(SQ|SQBAR|SQUAREBAR)(.+)$")
_PIPE_RE = re.compile(r"^(?:PIPE|P)(X{0,2})(\d+(?:\.\d+)?(?:-\d+/\d+)?)(STD|STANDARD|SCH40|XS|XH|SCH80|XXS|XXH)?$")
_PIPE_ALT_RE = re.compile(r"^(\d+(?:\.\d+)?(?:-\d+/\d+)?)(?:DIA|NPS)?(STD|STANDARD|SCH40|XS|XH|SCH80|XXS|XXH)PIPE$")


def _pipe_weight(nps_token: str, wall_code: str, prefix_x: str = "") -> UnitWeight | None:
    nps = _num(nps_token)
    if nps is None or nps not in _PIPE_NPS:
        return None
    od, std, xs, xxs = _PIPE_NPS[nps]
    code = (wall_code or "").upper()
    if prefix_x == "XX" or code in ("XXS", "XXH"):
        wall, label = xxs, "XXS"
    elif prefix_x == "X" or code in ("XS", "XH", "SCH80"):
        wall, label = xs, "XS"
    else:
        wall, label = std, "STD"
    if not wall:
        return None
    kgm = _imperial_area_to_kgm(_round_hollow_area(od, wall))
    return UnitWeight(kgm, "geometry", f"Pipe {nps:g} {label}: OD {od} in, wall {wall} in")


_METRIC_FAMILIES = {"SHS", "RHS", "CHS", "EA", "UA"}


def _is_imperial(dims: list[float], thickness: float, jurisdiction: str, family: str = "") -> bool:
    """Decide inch vs mm for a dimensioned section.

    Imperial sections never exceed ~1.25 in wall and ~44 in depth; metric walls start
    at 1.6 mm. A clear numeric signal wins; otherwise the jurisdiction decides.
    """
    if family in _METRIC_FAMILIES or thickness >= 1.5 or max(dims) > 48:
        return False
    if thickness <= 1.25 and max(dims) <= 30:
        return True
    return (jurisdiction or "USA").upper() == "USA"


def compute_unit_weight(profile: str, jurisdiction: str = "USA") -> UnitWeight | None:
    """Resolve a profile designation to kg/m. None when it cannot be derived."""
    p = normalise_profile(profile)
    if not p:
        return None
    j = (jurisdiction or "USA").upper()

    # ── 1. Designation carries the weight ────────────────────────────────────
    m = _ROLLED_RE.match(p)
    if m:
        family, depth, wt = m.group(1), float(m.group(2)), float(m.group(3))
        if family in ("WWF", "WRF") or depth >= 60:
            return UnitWeight(wt, "designation", f"{wt:g} kg/m from metric designation")
        return UnitWeight(wt * LBFT_TO_KGM, "designation", f"{wt:g} lb/ft from designation")

    m = _AUS_ROLLED_RE.match(p)
    if m:
        wt = float(m.group(3))
        return UnitWeight(wt, "designation", f"{wt:g} kg/m from designation")

    m = _AUS_PFC_RE.match(p)
    if m and int(m.group(1)) in _AUS_PFC_KGM:
        wt = _AUS_PFC_KGM[int(m.group(1))]
        return UnitWeight(wt, "table", f"{m.group(1)}PFC {wt:g} kg/m (OneSteel)")

    # ── 2. Pipe by nominal size ──────────────────────────────────────────────
    m = _PIPE_RE.match(p)
    if m:
        pw = _pipe_weight(m.group(2), m.group(3) or "", m.group(1) or "")
        if pw:
            return pw
    m = _PIPE_ALT_RE.match(p)
    if m:
        pw = _pipe_weight(m.group(1), m.group(2))
        if pw:
            return pw

    # ── 3. Hollow sections by geometry ───────────────────────────────────────
    m = _HOLLOW_RE.match(p)
    if m:
        d = _dims(m.group(2))
        if d and len(d) == 3:
            b, h, t = d
            if _is_imperial(d, t, j, m.group(1)):
                kgm = _imperial_area_to_kgm(_rect_hollow_area(b, h, t))
                return UnitWeight(kgm, "geometry", f"HSS {b:g}x{h:g}x{t:g} in, nominal wall")
            kgm = _metric_area_to_kgm(_rect_hollow_area(b, h, t))
            return UnitWeight(kgm, "geometry", f"Hollow {b:g}x{h:g}x{t:g} mm")
        if d and len(d) == 2:
            dia, t = d
            if _is_imperial(d, t, j, m.group(1)):
                kgm = _imperial_area_to_kgm(_round_hollow_area(dia, t))
                return UnitWeight(kgm, "geometry", f"Round HSS {dia:g}x{t:g} in")
            kgm = _metric_area_to_kgm(_round_hollow_area(dia, t))
            return UnitWeight(kgm, "geometry", f"Round hollow {dia:g}x{t:g} mm")

    # ── 4. Angles (single or back-to-back) ──────────────────────────────────
    m = _ANGLE_RE.match(p)
    if m:
        count = int(m.group(1)) if m.group(1) else 1
        d = _dims(m.group(3))
        if d and len(d) == 3:
            a, b, t = d
            area = _angle_area(a, b, t) * count
            if _is_imperial(d, t, j, m.group(2)):
                kgm = _imperial_area_to_kgm(area)
                unit = "in"
            else:
                kgm = _metric_area_to_kgm(area)
                unit = "mm"
            label = f"{count}x " if count > 1 else ""
            return UnitWeight(kgm, "geometry", f"{label}angle {a:g}x{b:g}x{t:g} {unit}")

    # ── 5. Plate / flat bar (unit weight per metre of length) ───────────────
    m = _PLATE_RE.match(p)
    if m:
        parts = m.group(2).split("X")
        t, w = (_num(parts[0]), _num(parts[1])) if len(parts) >= 2 else (None, None)
        if t and w and t > 0 and w > 0:
            inches = '"' in str(profile) or "/" in m.group(2)
            if not inches:
                inches = j == "USA" and t <= 4 and w <= 120
            if inches:
                kgm = _imperial_area_to_kgm(t * w)
                return UnitWeight(kgm, "geometry", f"Plate {t:g} x {w:g} in", is_plate=True)
            kgm = _metric_area_to_kgm(t * w)
            return UnitWeight(kgm, "geometry", f"Plate {t:g} x {w:g} mm", is_plate=True)

    # ── 6. Bars ──────────────────────────────────────────────────────────────
    m = _ROUND_BAR_RE.match(p)
    if m:
        d = _dims(m.group(2))
        if d and len(d) == 1:
            dia = d[0]
            if dia <= 6 and (j == "USA" or "/" in m.group(2)):
                return UnitWeight(_imperial_area_to_kgm(math.pi * dia * dia / 4), "geometry", f"Round bar {dia:g} in")
            return UnitWeight(_metric_area_to_kgm(math.pi * dia * dia / 4), "geometry", f"Round bar {dia:g} mm")
    m = _SQUARE_BAR_RE.match(p)
    if m:
        d = _dims(m.group(2))
        if d and len(d) == 1:
            s = d[0]
            if s <= 6 and (j == "USA" or "/" in m.group(2)):
                return UnitWeight(_imperial_area_to_kgm(s * s), "geometry", f"Square bar {s:g} in")
            return UnitWeight(_metric_area_to_kgm(s * s), "geometry", f"Square bar {s:g} mm")

    return None


def lookup_unit_weight(profile: str, jurisdiction: str = "USA") -> float | None:
    """kg/m for a profile (rounded to 0.01), or None. Backwards-compatible entry point."""
    uw = compute_unit_weight(profile, jurisdiction)
    return round(uw.kg_per_m, 2) if uw else None


# ─────────────────────────────────────────────────────────────────────────────
# Joists — supplied by the joist manufacturer, never part of the steel take-off
# ─────────────────────────────────────────────────────────────────────────────

# SJI designations: 18K4, 24LH06, 30KCS5, 48DLH13, 28SLH...; joist girders 48G8N10.5K.
_SJI_JOIST_RE = re.compile(r"^\d{1,3}(K|KCS|LH|DLH|SLH|CJ)\d", re.I)
_JOIST_GIRDER_RE = re.compile(r"^\d{1,3}(G|BG|VG)\d+N\d", re.I)


def is_joist(*fields: str) -> bool:
    """True when a take-off row is an open-web steel joist, joist girder or joist bridging."""
    for f in fields:
        s = str(f or "").upper()
        if not s:
            continue
        if "JOIST" in s or re.search(r"\bOWS?J\b", s):
            return True
        token = normalise_profile(s)
        if _SJI_JOIST_RE.match(token) or _JOIST_GIRDER_RE.match(token):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Length parsing
# ─────────────────────────────────────────────────────────────────────────────

_FT_IN_RE = re.compile(
    r"""^\s*
    (?:(?P<ft>\d+(?:\.\d+)?)\s*')?                    # feet
    \s*-?\s*
    (?:
        (?P<in>\d+(?:\.\d+)?)                          # whole inches
        (?:\s*[-\s]\s*(?P<num>\d+)\s*/\s*(?P<den>\d+))?  # + fraction
      | (?P<fnum>\d+)\s*/\s*(?P<fden>\d+)              # fraction only
    )?
    \s*"?\s*$""",
    re.X,
)


def parse_length_mm(raw: str, jurisdiction: str = "USA") -> float | None:
    """Convert a printed length to millimetres.

    Imperial: 24'-6", 24'-6 3/8", 24' 6-3/8", 24'-0", 24', 6 1/2", 290", 24.5'.
    Metric:   7468, 7,468, 7468 mm, 7.468 m.
    Grid annotations ('[Grid A-B]'), VIF and blanks return None / are stripped.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("—", "-", "–"):
        return None
    s = re.sub(r"\[.*?\]", "", s)                       # [Grid A-B]
    s = s.replace("′", "'").replace("’", "'").replace("″", '"').replace("”", '"').replace("“", '"')
    s = s.replace("''", '"').strip()
    up = s.upper()
    if up.startswith("VIF") or "SEE" in up or "VARIES" in up:
        return None

    if "'" in s or '"' in s:
        m = _FT_IN_RE.match(s)
        if not m:
            return None
        ft = float(m.group("ft")) if m.group("ft") else 0.0
        inches = 0.0
        if m.group("in"):
            inches = float(m.group("in"))
            if m.group("num") and m.group("den") and float(m.group("den")):
                inches += float(m.group("num")) / float(m.group("den"))
        elif m.group("fnum") and m.group("fden") and float(m.group("fden")):
            inches = float(m.group("fnum")) / float(m.group("fden"))
        total_mm = ft * 304.8 + inches * IN_TO_MM
        return round(total_mm, 1) if total_mm > 0 else None

    m = re.match(r"^([\d.,\s]+)\s*(MM|M)?$", up)
    if m:
        num = m.group(1).replace(",", "").replace(" ", "")
        try:
            v = float(num)
        except ValueError:
            return None
        if m.group(2) == "M":
            v *= 1000.0
        return round(v, 1) if v > 0 else None
    return None


def format_ft_in(mm: float) -> str:
    """Millimetres → nearest 1/16 in, written 24'-6 3/8"."""
    if not mm or mm <= 0:
        return "—"
    total_in = mm / IN_TO_MM
    sixteenths = round(total_in * 16)
    ft, rem = divmod(sixteenths, 12 * 16)
    whole_in, frac16 = divmod(rem, 16)
    frac = ""
    if frac16:
        g = math.gcd(frac16, 16)
        frac = f" {frac16 // g}/{16 // g}"
    return f"{ft}'-{whole_in}{frac}\""


# ─────────────────────────────────────────────────────────────────────────────
# Weight math
# ─────────────────────────────────────────────────────────────────────────────

def plate_weight_kg(thickness_mm: float, width_mm: float, length_m: float) -> float:
    """Volumetric plate/bar weight: t(mm) x w(mm) x L(m) x 0.00785."""
    return round(thickness_mm * width_mm * length_m * 0.00785, 3)


def member_weight_kg(qty: float, length_m: float, unit_weight_kgm: float) -> float:
    """Linear-member weight: Qty x Length(m) x unit weight(kg/m)."""
    return round(qty * length_m * unit_weight_kgm, 3)


def kg_to_lb(kg: float) -> float:
    return round(kg * KG_TO_LB, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt reference text
# ─────────────────────────────────────────────────────────────────────────────

_REFERENCE_SAMPLES = {
    "USA": [
        "W8x31", "W10x33", "W12x26", "W14x48", "W16x31", "W18x35", "W21x44", "W24x55",
        "C10x15.3", "MC12x31", "WT6x20", "HSS4x4x1/4", "HSS6x6x3/8", "HSS8x8x1/2",
        "HSS8x4x1/4", "HSS6.625x0.280", "Pipe4STD", "Pipe6XS", "L3x3x1/4", "L4x4x3/8",
        "L6x4x1/2", "2L4x4x3/8", "PL1/2x8",
    ],
    "CANADA": [
        "W310x52", "W360x79", "W410x60", "W530x92", "WWF900x169", "HSS127x127x6.4",
        "HSS152x102x9.5", "HSS168x6.4", "L102x102x9.5", "C310x31", "PL12x200",
    ],
    "AUSTRALIA": [
        "310UB46.2", "410UB59.7", "250UC89.5", "150PFC", "SHS100x100x6",
        "RHS150x100x6", "CHS168.3x6.4", "EA100x100x8", "UA150x90x10", "PL12x200",
    ],
}


def weight_table_text(jurisdiction: str = "USA") -> str:
    """Reference block for prompts — every figure generated by the same calculator the
    platform uses to verify the take-off, so prompt and verification never disagree."""
    j = (jurisdiction or "USA").upper()
    if j not in _REFERENCE_SAMPLES:
        j = "USA"
    lines = []
    for jj in (j,) + tuple(x for x in JURISDICTIONS if x != j):
        samples = []
        for prof in _REFERENCE_SAMPLES[jj]:
            uw = compute_unit_weight(prof, jj)
            if uw:
                samples.append(f"{prof}={uw.kg_per_m:.1f}")
        lines.append(f"  {jj}: " + "  ".join(samples))
    return (
        "UNIT WEIGHTS (kg/m) — the platform recomputes every weight from the profile "
        "designation, so write each profile EXACTLY as printed on the drawing.\n"
        "  Rolled shapes: imperial W/M/S/HP/C/MC/WT = lb/ft after the 'x' (x 1.4882 = kg/m); "
        "metric W/WWF/C (CISC) = kg/m after the 'x'; UB/UC = kg/m after the code.\n"
        "  HSS/RHS/SHS: A = 2t(B+H) - 4t^2 - 3(4-pi)t^2 on nominal wall; round HSS/CHS/pipe: "
        "A = pi(D-t)t; angles: A = t(a+b-t); plate: t x w. Steel 7850 kg/m^3 (490 lb/ft^3).\n"
        "  Verified samples:\n" + "\n".join(lines)
    )


# Kept for callers that test membership of a jurisdiction.
UNIT_WEIGHTS: dict[str, dict] = {j: {} for j in JURISDICTIONS}
