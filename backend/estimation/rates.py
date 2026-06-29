"""
Country-wise market rate matrix.
All commercial figures are in LOCAL CURRENCY of the country (precomputed for that market).
Internal "cost" rates are tracked separately in USD as a global benchmark — never exposed to non-estimator roles.
"""
from __future__ import annotations

# Internal global cost basis (hidden — only estimator/admin sees these)
INTERNAL_DETAILER_COST_USD = (18.0, 25.0)            # USD/hr — detailing cost basis (estimate anchor)
INTERNAL_ENGINEER_COST_USD = (35.0, 55.0)            # USD/hr — engineer cost basis
INTERNAL_PM_COST_USD       = (32.0, 50.0)            # USD/hr
INTERNAL_FABRICATOR_COST_PER_TON_USD = (2200.0, 2900.0)   # cost basis (excl. margin)

# Country matrix — billable / quote rates (what client sees)
COUNTRY_RATES: dict[str, dict] = {
    # ─────────── USD-region anchor (US) ───────────
    "USA": {
        "currency": "USD",
        "symbol":   "$",
        "labour_index":   1.00,
        "material_index": 1.00,
        "tax_pct":        0.0,
        "detailer_hourly":      (52, 72),
        "engineer_hourly":      (95, 145),
        "pm_hourly":            (75, 120),
        "fabricator_per_ton":   (3000, 4200),
        "erection_per_ton":     (950, 1400),
        "modular_per_module":   (14000, 32000),
    },
    "Canada": {
        "currency": "CAD",
        "symbol":   "C$",
        "labour_index":   0.92,
        "material_index": 0.97,
        "tax_pct":        5.0,   # GST
        "detailer_hourly":      (62, 88),
        "engineer_hourly":      (115, 175),
        "pm_hourly":            (90, 145),
        "fabricator_per_ton":   (3600, 5100),
        "erection_per_ton":     (1150, 1700),
        "modular_per_module":   (17000, 38000),
    },
    "UK": {
        "currency": "GBP",
        "symbol":   "£",
        "labour_index":   0.95,
        "material_index": 1.05,
        "tax_pct":        20.0,
        "detailer_hourly":      (42, 60),
        "engineer_hourly":      (80, 125),
        "pm_hourly":            (62, 100),
        "fabricator_per_ton":   (2500, 3500),
        "erection_per_ton":     (800, 1200),
        "modular_per_module":   (12000, 28000),
    },
    "UAE": {
        "currency": "AED",
        "symbol":   "AED",
        "labour_index":   0.55,
        "material_index": 1.08,
        "tax_pct":        5.0,   # VAT
        "detailer_hourly":      (95, 145),
        "engineer_hourly":      (220, 360),
        "pm_hourly":            (170, 275),
        "fabricator_per_ton":   (8500, 12500),
        "erection_per_ton":     (2400, 3800),
        "modular_per_module":   (38000, 92000),
    },
    "Australia": {
        "currency": "AUD",
        "symbol":   "A$",
        "labour_index":   1.10,
        "material_index": 1.05,
        "tax_pct":        10.0,  # GST
        "detailer_hourly":      (85, 120),
        "engineer_hourly":      (160, 240),
        "pm_hourly":            (130, 200),
        "fabricator_per_ton":   (4500, 6200),
        "erection_per_ton":     (1500, 2200),
        "modular_per_module":   (22000, 48000),
    },
    "India": {
        "currency": "INR",
        "symbol":   "₹",
        "labour_index":   0.18,
        "material_index": 0.85,
        "tax_pct":        18.0,  # GST
        "detailer_hourly":      (650, 1100),
        "engineer_hourly":      (1400, 2400),
        "pm_hourly":            (1100, 1800),
        "fabricator_per_ton":   (95000, 135000),
        "erection_per_ton":     (28000, 42000),
        "modular_per_module":   (480000, 1100000),
    },
    "Europe": {
        "currency": "EUR",
        "symbol":   "€",
        "labour_index":   1.00,
        "material_index": 1.02,
        "tax_pct":        19.0,  # avg EU VAT
        "detailer_hourly":      (48, 70),
        "engineer_hourly":      (92, 140),
        "pm_hourly":            (72, 115),
        "fabricator_per_ton":   (2700, 3900),
        "erection_per_ton":     (880, 1300),
        "modular_per_module":   (13000, 30000),
    },
    "Saudi Arabia": {
        "currency": "SAR",
        "symbol":   "SAR",
        "labour_index":   0.58,
        "material_index": 1.04,
        "tax_pct":        15.0,
        "detailer_hourly":      (90, 135),
        "engineer_hourly":      (210, 340),
        "pm_hourly":            (165, 260),
        "fabricator_per_ton":   (8200, 12000),
        "erection_per_ton":     (2300, 3500),
        "modular_per_module":   (36000, 88000),
    },
    "Singapore": {
        "currency": "SGD",
        "symbol":   "S$",
        "labour_index":   1.08,
        "material_index": 1.06,
        "tax_pct":        9.0,
        "detailer_hourly":      (72, 105),
        "engineer_hourly":      (135, 210),
        "pm_hourly":            (108, 170),
        "fabricator_per_ton":   (4200, 5800),
        "erection_per_ton":     (1350, 2000),
        "modular_per_module":   (20000, 44000),
    },
}


def country(code: str) -> dict:
    """Return country rate row. Defaults to USA if unknown."""
    return COUNTRY_RATES.get(code) or COUNTRY_RATES["USA"]


def supported_countries() -> list[dict]:
    return [
        {"code": k, "currency": v["currency"], "symbol": v["symbol"]}
        for k, v in COUNTRY_RATES.items()
    ]
