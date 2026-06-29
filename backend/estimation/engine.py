"""Deterministic per-role estimation engine — 3-role architecture.

Only Detailer + Fabricator have calculators. Super admin can run either.
Fabricator estimation REQUIRES a user-provided per-ton cost range
(`cost_per_ton_low`, `cost_per_ton_high`); the report is anchored on that band.
"""
from __future__ import annotations

from .rates import country
from .sanity import sanity_check


# ============================================================
# COMPLEXITY MULTIPLIERS
# ============================================================
COMPLEXITY = {
    "Low":      {"mult": 0.85, "drawings_hr": 1.5},
    "Medium":   {"mult": 1.00, "drawings_hr": 2.5},
    "High":     {"mult": 1.35, "drawings_hr": 4.0},
    "AESS":     {"mult": 1.80, "drawings_hr": 6.5},
    "Critical": {"mult": 2.10, "drawings_hr": 8.0},
}

MATERIAL_FACTOR = {
    "Carbon Steel":      1.00,
    "HSS":               1.05,
    "Galvanised":        1.18,
    "Stainless":         1.55,
    "Weathering (A588)": 1.12,
    "Aluminium":         1.62,
}

SURFACE_FACTOR = {
    "Shop primer only":       1.00,
    "SSPC-SP6 + 2-coat":      1.08,
    "SSPC-SP10 + 3-coat":     1.18,
    "Hot-dip galvanised":     1.28,
    "Intumescent fire-rated": 1.45,
}

ASSEMBLY_FACTOR = {
    "Simple bolted":      1.00,
    "Mixed bolt/weld":    1.10,
    "Heavy welded":       1.22,
    "Architectural AESS": 1.40,
    "Custom curved":      1.55,
}


def _midpoint(rng: tuple[float, float]) -> float:
    return (rng[0] + rng[1]) / 2.0


def _round(x: float) -> float:
    return round(x, 2)


def _format_money(amount: float, c: dict) -> str:
    sym = c["symbol"]
    if c["currency"] in ("INR", "AED", "SAR"):
        return f"{sym} {amount:,.0f}"
    return f"{sym}{amount:,.0f}"


# ============================================================
# DETAILER  — hours-based
# ============================================================
def calculate_detailer(inputs: dict, country_code: str = "USA") -> dict:
    c = country(country_code)
    drawings    = max(1, int(inputs.get("drawings", 40)))
    complexity  = inputs.get("complexity", "Medium")
    revisions   = max(0, int(inputs.get("revisions", 1)))
    modeling_h  = float(inputs.get("modeling_hours", 0))
    checking_h  = float(inputs.get("checking_hours", 0))
    connections = max(0, int(inputs.get("connection_count", 0)))

    comp = COMPLEXITY.get(complexity, COMPLEXITY["Medium"])
    base_dwg_hours  = drawings * comp["drawings_hr"]
    connection_hrs  = connections * 0.35
    rev_factor      = 1 + revisions * 0.15
    total_hours     = (base_dwg_hours + connection_hrs) * rev_factor + modeling_h + checking_h

    billable_rate = _midpoint(c["detailer_hourly"])
    final_amount  = total_hours * billable_rate

    breakdown = [
        {"item": "Production drawings",  "hours": _round(base_dwg_hours),  "qty": drawings,    "unit": "dwgs"},
        {"item": "Connection detailing", "hours": _round(connection_hrs),  "qty": connections, "unit": "conn"},
        {"item": "Revisions overhead",   "hours": _round((base_dwg_hours + connection_hrs) * (rev_factor - 1)),
                                                                              "qty": revisions, "unit": "cycles"},
        {"item": "3D modeling",          "hours": _round(modeling_h),      "qty": 1, "unit": "lot"},
        {"item": "QC / checking",        "hours": _round(checking_h),      "qty": 1, "unit": "lot"},
    ]

    sanity = sanity_check(role="detailer", total=final_amount, drawings=drawings, currency=c["currency"])
    return {
        "role": "detailer",
        "country": country_code,
        "currency": c["currency"],
        "symbol": c["symbol"],
        "visible": {
            "total_hours":   _round(total_hours),
            "scope_summary": f"{drawings} drawings · {complexity} complexity · {revisions} revision cycle(s)",
            "deliverables": [
                "Production shop drawings (issued for fabrication)",
                "Connection details and weld map",
                "Anchor-bolt setting plan",
                "3D coordination model (LOD 350)",
                "QC checklist sign-off package",
            ],
            "timeline_weeks": _round(total_hours / 38),
            "final_amount":   _format_money(final_amount, c),
            "final_amount_raw": _round(final_amount),
        },
        "breakdown": breakdown,
        "meta": {
            "complexity": complexity,
            "complexity_multiplier": comp["mult"],
            "productivity_hrs_per_drawing": comp["drawings_hr"],
            "sanity": sanity,
        },
    }


# ============================================================
# FABRICATOR — tonnage × USER-PROVIDED per-ton band
# ============================================================
def calculate_fabricator(inputs: dict, country_code: str = "USA") -> dict:
    """Fabricator MUST provide cost_per_ton_low and cost_per_ton_high. Final report is
    a range driven by those user inputs, modulated by material / surface / assembly factors."""
    c = country(country_code)
    tonnage         = max(0.1, float(inputs.get("tonnage", 100)))
    low             = float(inputs.get("cost_per_ton_low", 0))
    high            = float(inputs.get("cost_per_ton_high", 0))
    if low <= 0 or high <= 0:
        raise ValueError("Provide cost_per_ton_low and cost_per_ton_high (both > 0).")
    if high < low:
        low, high = high, low

    material  = inputs.get("material_type", "Carbon Steel")
    weld_in   = float(inputs.get("weld_inches", 0))
    cut_m     = float(inputs.get("cut_meters", 0))
    surface   = inputs.get("surface_treatment", "Shop primer only")
    assembly  = inputs.get("assembly_complexity", "Simple bolted")

    mat_f  = MATERIAL_FACTOR.get(material, 1.0)
    surf_f = SURFACE_FACTOR.get(surface, 1.0)
    asm_f  = ASSEMBLY_FACTOR.get(assembly, 1.0)
    composite_factor = mat_f * surf_f * asm_f

    rate_low_adj   = low  * composite_factor
    rate_high_adj  = high * composite_factor
    rate_mid_adj   = (rate_low_adj + rate_high_adj) / 2.0

    subtotal_low  = rate_low_adj  * tonnage
    subtotal_mid  = rate_mid_adj  * tonnage
    subtotal_high = rate_high_adj * tonnage

    # Process split applied to MID for breakdown narrative
    process_split = {
        "Material (mill sections + plate)":   0.35,
        "Shop labour (cut/weld/fit)":         0.30,
        "Coatings / surface prep":            0.12,
        "Consumables (weld wire / bolts)":    0.06,
        "Equipment / burn-machine":           0.07,
        "Overhead / QA-QC":                   0.06,
        "Margin":                             0.04,
    }
    process_breakdown = [
        {
            "process": label,
            "share":   f"{pct*100:.0f}%",
            "amount":  _format_money(subtotal_mid * pct, c),
            "amount_raw": _round(subtotal_mid * pct),
        }
        for label, pct in process_split.items()
    ]

    tax_low  = subtotal_low  * c["tax_pct"] / 100.0
    tax_mid  = subtotal_mid  * c["tax_pct"] / 100.0
    tax_high = subtotal_high * c["tax_pct"] / 100.0
    grand_low  = subtotal_low  + tax_low
    grand_mid  = subtotal_mid  + tax_mid
    grand_high = subtotal_high + tax_high

    sanity = sanity_check(role="fabricator", total=subtotal_mid, tonnage=tonnage, currency=c["currency"])

    return {
        "role": "fabricator",
        "country": country_code,
        "currency": c["currency"],
        "symbol": c["symbol"],
        "visible": {
            "tonnage": _round(tonnage),
            "user_rate_low":  _format_money(low, c),
            "user_rate_high": _format_money(high, c),
            "adjusted_rate_low":  _format_money(rate_low_adj, c),
            "adjusted_rate_high": _format_money(rate_high_adj, c),
            "adjusted_rate_mid":  _format_money(rate_mid_adj, c),
            "composite_factor":   _round(composite_factor),
            "process_breakdown":  process_breakdown,
            "subtotal_low":   _format_money(subtotal_low, c),
            "subtotal_mid":   _format_money(subtotal_mid, c),
            "subtotal_high":  _format_money(subtotal_high, c),
            "tax_label":      f"Tax ({c['tax_pct']:.0f}%)",
            "tax_low":   _format_money(tax_low, c),
            "tax_mid":   _format_money(tax_mid, c),
            "tax_high":  _format_money(tax_high, c),
            "grand_low":  _format_money(grand_low, c),
            "grand_mid":  _format_money(grand_mid, c),
            "grand_high": _format_money(grand_high, c),
            "grand_range_text": (
                f"{_format_money(grand_low, c)}  →  {_format_money(grand_high, c)}"
            ),
            "grand_mid_raw":  _round(grand_mid),
            "grand_low_raw":  _round(grand_low),
            "grand_high_raw": _round(grand_high),
            "final_amount":     _format_money(grand_mid, c),    # midpoint as headline
            "final_amount_raw": _round(grand_mid),
        },
        "breakdown": [
            {"item": "User per-ton low",       "qty": _format_money(low, c),  "unit": "/ ton"},
            {"item": "User per-ton high",      "qty": _format_money(high, c), "unit": "/ ton"},
            {"item": "Welding (linear inches)", "qty": weld_in, "unit": "in"},
            {"item": "Cutting (linear meters)", "qty": cut_m,   "unit": "m"},
            {"item": "Material type",           "qty": material, "unit": "",  "rate": f"factor ×{mat_f:.2f}"},
            {"item": "Surface treatment",       "qty": surface,  "unit": "",  "rate": f"factor ×{surf_f:.2f}"},
            {"item": "Assembly complexity",     "qty": assembly, "unit": "",  "rate": f"factor ×{asm_f:.2f}"},
        ],
        "meta": {
            "sanity": sanity,
            "composite_factor": _round(composite_factor),
            "labour_index": c.get("labour_index", 1.0),
            "material_index": c.get("material_index", 1.0),
        },
    }


# ============================================================
# AI-DRIVEN ESTIMATION (drawing-driven, only LOW/HIGH user inputs)
# ============================================================
def apply_band_to_extracted(
    *,
    role: str,
    extracted: dict,
    rate_low: float,
    rate_high: float,
    country_code: str = "USA",
) -> dict:
    """Apply user-supplied LOW/HIGH rate band to AI-extracted quantities.

    Fabricator: rate is per metric ton.
    Detailer:   rate is per production drawing.
    """
    c = country(country_code)
    if rate_low <= 0 or rate_high <= 0:
        raise ValueError("Both LOW and HIGH rates must be > 0.")
    if rate_high < rate_low:
        rate_low, rate_high = rate_high, rate_low

    if role == "fabricator":
        tonnage = float(extracted.get("tonnage", 0) or 0)
        if tonnage <= 0:
            raise ValueError("AI could not extract a usable tonnage from the drawings.")
        subtotal_low  = tonnage * rate_low
        subtotal_high = tonnage * rate_high
        subtotal_mid  = (subtotal_low + subtotal_high) / 2.0
        tax_low       = subtotal_low  * c["tax_pct"] / 100.0
        tax_mid       = subtotal_mid  * c["tax_pct"] / 100.0
        tax_high      = subtotal_high * c["tax_pct"] / 100.0
        grand_low     = subtotal_low  + tax_low
        grand_mid     = subtotal_mid  + tax_mid
        grand_high    = subtotal_high + tax_high
        sanity = sanity_check(role=role, total=subtotal_mid, tonnage=tonnage, currency=c["currency"])

        return {
            "role": role,
            "country": country_code,
            "currency": c["currency"],
            "symbol":   c["symbol"],
            "visible": {
                "extracted": {
                    "tonnage":          _round(tonnage),
                    "members_counted":  extracted.get("members_counted", 0),
                    "primary_material": extracted.get("primary_material", ""),
                    "drawings_seen":    extracted.get("drawings_seen", 0),
                    "notes":            extracted.get("notes", ""),
                },
                "user_rate_low":  _format_money(rate_low,  c),
                "user_rate_high": _format_money(rate_high, c),
                "subtotal_low":   _format_money(subtotal_low,  c),
                "subtotal_mid":   _format_money(subtotal_mid,  c),
                "subtotal_high":  _format_money(subtotal_high, c),
                "tax_label":      f"Tax ({c['tax_pct']:.0f}%)",
                "tax_low":  _format_money(tax_low,  c),
                "tax_mid":  _format_money(tax_mid,  c),
                "tax_high": _format_money(tax_high, c),
                "grand_low":  _format_money(grand_low,  c),
                "grand_mid":  _format_money(grand_mid,  c),
                "grand_high": _format_money(grand_high, c),
                "grand_range_text": f"{_format_money(grand_low, c)}  →  {_format_money(grand_high, c)}",
                "final_amount":     _format_money(grand_mid, c),
                "final_amount_raw": _round(grand_mid),
            },
            "breakdown": [
                {"item": "Total fabricated tonnage", "qty": _round(tonnage), "unit": "t"},
                {"item": "Per-ton rate — low",       "qty": _format_money(rate_low,  c), "unit": "/ ton"},
                {"item": "Per-ton rate — high",      "qty": _format_money(rate_high, c), "unit": "/ ton"},
            ],
            "meta": {
                "engine": "STRUCTMIND CORE",
                "sanity": sanity,
                "extracted_raw": extracted,
            },
        }

    if role == "detailer":
        # Hours-based: detailing effort (hours) × the per-hour rate band.
        total_hours = float(extracted.get("total_hours", 0) or 0)
        drawings = int(extracted.get("drawings", 0) or 0)
        if total_hours <= 0:
            # Derive hours from drawings × complexity productivity if hours weren't given.
            comp = COMPLEXITY.get(extracted.get("complexity", "Medium"), COMPLEXITY["Medium"])
            if drawings <= 0:
                raise ValueError("Could not determine detailing hours or drawing count from the drawings.")
            total_hours = drawings * comp["drawings_hr"]

        subtotal_low  = total_hours * rate_low
        subtotal_high = total_hours * rate_high
        subtotal_mid  = (subtotal_low + subtotal_high) / 2.0
        sanity = sanity_check(role=role, total=subtotal_mid, drawings=drawings, currency=c["currency"])

        return {
            "role": role,
            "country": country_code,
            "currency": c["currency"],
            "symbol":   c["symbol"],
            "visible": {
                "extracted": {
                    "total_hours":           _round(total_hours),
                    "drawings":              drawings,
                    "connections":           extracted.get("connections", 0),
                    "complexity":            extracted.get("complexity", "Medium"),
                    "drawings_seen":         extracted.get("drawings_seen", 0),
                    "notes":                 extracted.get("notes", ""),
                },
                "total_hours":     _round(total_hours),
                "user_rate_low":   _format_money(rate_low,  c),
                "user_rate_high":  _format_money(rate_high, c),
                "rate_basis":      f"{_format_money(rate_low, c)} – {_format_money(rate_high, c)} / hr",
                "timeline_weeks":  _round(total_hours / 38),
                "subtotal_low":   _format_money(subtotal_low,  c),
                "subtotal_mid":   _format_money(subtotal_mid,  c),
                "subtotal_high":  _format_money(subtotal_high, c),
                "grand_low":   _format_money(subtotal_low,  c),
                "grand_mid":   _format_money(subtotal_mid,  c),
                "grand_high":  _format_money(subtotal_high, c),
                "grand_range_text": f"{_format_money(subtotal_low, c)}  →  {_format_money(subtotal_high, c)}",
                "final_amount":     _format_money(subtotal_mid, c),
                "final_amount_raw": _round(subtotal_mid),
            },
            "breakdown": [
                {"item": "Detailing hours",       "qty": _round(total_hours), "unit": "hrs"},
                {"item": "Production drawings",   "qty": drawings, "unit": "dwgs"},
                {"item": "Connections",           "qty": extracted.get("connections", 0), "unit": "conn"},
                {"item": "Rate per hour — low",   "qty": _format_money(rate_low,  c), "unit": "/ hr"},
                {"item": "Rate per hour — high",  "qty": _format_money(rate_high, c), "unit": "/ hr"},
                {"item": "Complexity",            "qty": extracted.get("complexity", "Medium"), "unit": ""},
            ],
            "meta": {
                "engine": "STRUCTMIND CORE",
                "sanity": sanity,
                "extracted_raw": extracted,
            },
        }

    raise ValueError(f"AI estimation only supports detailer or fabricator (got '{role}').")


# ============================================================
# DISPATCHER (only detailer + fabricator + super_admin)
# ============================================================
CALCULATORS = {
    "detailer":    calculate_detailer,
    "fabricator":  calculate_fabricator,
    "super_admin": calculate_fabricator,  # super_admin can run either; resolved by request `role`
}


def calculate(role: str, inputs: dict, country_code: str = "USA") -> dict:
    role = (role or "").lower()
    fn = CALCULATORS.get(role)
    if not fn:
        raise ValueError(f"No estimation calculator for role '{role}'. Allowed: detailer, fabricator.")
    return fn(inputs, country_code)
