"""Fabricator Estimation Pro — tonnage-driven cost estimate, computed deterministically.

The tonnage comes from the consolidated take-off (estimation/takeoff.py); this module
applies the rate band, composite adjustment factors and tax, and renders the full
estimate. Every figure in the report is computed once here, so the manifest, executive
summary, category build-up, process split, waterfall and conversion summary agree to the
dollar by construction — no "LOCK value" can drift between sections.

Pricing ton: USA prices per short ton (2,000 lb), Canada/Australia per tonne (1,000 kg).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from estimation import weights as W
from estimation.rates import country as country_rates
from estimation.takeoff import SHORT_TON_KG, fmt, md_table, round_half_up, ton_definition, ton_label

DEFAULT_RATES = {   # (low, mid, high) per pricing ton, local currency
    "USA": (3500.0, 4000.0, 4500.0),
}

MATERIAL_FACTOR = {
    "Carbon Steel": 1.00, "HSS": 1.05, "Galvanised": 1.18, "Stainless": 1.55,
    "Weathering": 1.12, "Aluminium": 1.62,
}
SURFACE_FACTOR = {
    "Shop primer only": 1.00, "SSPC-SP6 + 2-coat": 1.08, "SSPC-SP10 + 3-coat": 1.18,
    "Hot-dip galvanised": 1.28, "Intumescent fire-rated": 1.45,
}
ASSEMBLY_FACTOR = {
    "Simple bolted": 1.00, "Mixed bolt/weld": 1.10, "Heavy welded": 1.22,
    "Architectural AESS": 1.40, "Custom curved": 1.55,
}
PROCESS_SPLIT = [
    ("Material — mill sections and plate", 0.35, "Structural mill steel"),
    ("Shop labour — cut, fit and weld", 0.30, "Welding to AWS D1.1 / CSA W59 / AS-NZS 1554"),
    ("Coatings and surface preparation", 0.12, "Per the specified surface system"),
    ("Consumables — wire, gas, bolts", 0.06, "Per-ton standard allowance"),
    ("Equipment — CNC, burn table, drill line", 0.07, "Machine time"),
    ("Overhead and QA/QC", 0.06, "Inspection, NDT, project management"),
    ("Shop margin", 0.04, "Fabricator margin"),
]
_JURIS_COUNTRY = {"USA": "USA", "CANADA": "Canada", "AUSTRALIA": "Australia"}
_COUNTRY_ALIASES = {
    "US": "USA", "USA": "USA", "UNITEDSTATES": "USA", "AMERICA": "USA",
    "CA": "Canada", "CAN": "Canada", "CANADA": "Canada",
    "AU": "Australia", "AUS": "Australia", "AUSTRALIA": "Australia",
    "UK": "UK", "GB": "UK", "UNITEDKINGDOM": "UK", "UAE": "UAE", "INDIA": "India", "IN": "India",
    "EUROPE": "Europe", "EU": "Europe", "SAUDIARABIA": "Saudi Arabia", "KSA": "Saudi Arabia",
    "SINGAPORE": "Singapore", "SG": "Singapore",
}


def country_code_fn(value: str | None, jurisdiction: str = "USA") -> str:
    key = re.sub(r"[^A-Z]", "", str(value or "").upper())
    return _COUNTRY_ALIASES.get(key) or _JURIS_COUNTRY.get((jurisdiction or "USA").upper(), "USA")


def _match(value: str, table: dict, default: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return default
    for k in table:
        if k.lower() == v:
            return k
    for k in table:
        kk = re.sub(r"[^a-z]", "", k.lower())
        if kk and (kk in re.sub(r"[^a-z]", "", v) or re.sub(r"[^a-z]", "", v) in kk):
            return k
    if "galv" in v:
        return "Hot-dip galvanised" if table is SURFACE_FACTOR else ("Galvanised" if table is MATERIAL_FACTOR else default)
    if "aess" in v:
        return "Architectural AESS" if table is ASSEMBLY_FACTOR else default
    if "weld" in v and table is ASSEMBLY_FACTOR:
        return "Heavy welded" if "heavy" in v else "Mixed bolt/weld"
    return default


def _round100(x: float) -> float:
    return round_half_up(x / 100.0) * 100.0


def _money(sym: str, x: float, decimals: int = 0) -> str:
    return f"{sym}{x:,.{decimals}f}"


def _num(v) -> float:
    try:
        return float(re.sub(r"[^0-9.]", "", str(v))) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def compute(takeoff: dict, *, country_code: str | None = None) -> dict:
    """All locked estimate figures from a consolidated take-off."""
    j = takeoff.get("jurisdiction", "USA")
    ov = takeoff.get("user_overrides") or {}
    ccode = country_code_fn(country_code or ov.get("country"), j)
    c = country_rates(ccode)
    sym, currency = c["symbol"], c["currency"]
    ton_key, ton_name = ton_label(j)
    totals = takeoff["totals"]
    tons = totals["gross_short_tons"] if ton_key == "short_tons" else totals["gross_t"]

    rl, rm, rh = _num(ov.get("rate_low")), _num(ov.get("rate_mid")), _num(ov.get("rate_high"))
    if rl > 0 and rh > 0:
        rate_basis = "USER-PROVIDED"
        rl, rh = min(rl, rh), max(rl, rh)
        rm = rm if rm > 0 else (rl + rh) / 2
    else:
        rate_basis = "DEFAULT"
        if j in DEFAULT_RATES:
            rl, rm, rh = DEFAULT_RATES[j]
        else:
            lo, hi = c["fabricator_per_ton"]
            rl, rh = float(lo), float(hi)
            rm = (rl + rh) / 2

    basis = takeoff.get("estimating_basis") or {}
    mat = _match(ov.get("material_type") or (basis.get("material_type") or {}).get("value"), MATERIAL_FACTOR, "Carbon Steel")
    surf = _match(ov.get("surface_treatment") or (basis.get("surface_treatment") or {}).get("value"), SURFACE_FACTOR, "Shop primer only")
    asm = _match(ov.get("assembly_complexity") or (basis.get("assembly_complexity") or {}).get("value"), ASSEMBLY_FACTOR, "Simple bolted")
    mat_f, surf_f, asm_f = MATERIAL_FACTOR[mat], SURFACE_FACTOR[surf], ASSEMBLY_FACTOR[asm]
    galv_note = ""
    if mat == "Galvanised" and surf == "Hot-dip galvanised":
        # Galvanizing is priced once — through the surface factor.
        mat_f = 1.00
        galv_note = "Galvanizing is carried once, in the surface factor; material factor set to x1.00."
    composite = mat_f * surf_f * asm_f

    tax = _num(ov.get("tax_pct")) if ov.get("tax_pct") not in (None, "") else float(c["tax_pct"])
    adj = {k: r * composite for k, r in (("low", rl), ("mid", rm), ("high", rh))}
    sub = {k: tons * a for k, a in adj.items()}
    lock = {k: _round100(s * (1 + tax / 100.0)) for k, s in sub.items()}
    per_ton_mid = round_half_up(lock["mid"] / tons) if tons else 0

    # Category tonnage (pricing tons) — net categories + allowances = total tons.
    div = SHORT_TON_KG if ton_key == "short_tons" else 1000.0
    cats = [(cat["category"], cat["weight_kg"] / div) for cat in takeoff["categories"]]
    cats += [(a["label"], a["kg"] / div) for a in totals["allowances"]]

    return {
        "jurisdiction": j, "country": ccode, "currency": currency, "symbol": sym,
        "ton_key": ton_key, "ton_name": ton_name, "tons": round(tons, 2), "tons_exact": tons,
        "rate_basis": rate_basis, "rates": {"low": rl, "mid": rm, "high": rh},
        "material": mat, "surface": surf, "assembly": asm,
        "factors": {"material": mat_f, "surface": surf_f, "assembly": asm_f, "composite": composite},
        "galv_note": galv_note, "tax_pct": tax, "adj_rates": adj, "subtotals": sub, "lock": lock,
        "per_ton_mid": per_ton_mid, "category_tons": cats,
        "quote_requested": bool(ov.get("quote_requested")),
    }


def render(takeoff: dict, est: dict, *, user_text: str = "") -> str:
    sym, cur = est["symbol"], est["currency"]
    tn = est["ton_name"]
    tons = est["tons"]
    f = est["factors"]
    lock, adj, sub = est["lock"], est["adj_rates"], est["subtotals"]
    t = takeoff["totals"]
    proj = takeoff.get("project") or {}
    basis = takeoff.get("estimating_basis") or {}
    quote = est["quote_requested"] or bool(re.search(r"\b(quote|quotation|proposal|client[- ]facing)\b", user_text or "", re.I))
    parts: list[str] = []
    m = lambda x: _money(sym, x)  # noqa: E731

    parts.append("## CALCULATION MANIFEST — SINGLE SOURCE OF TRUTH")
    parts.append("Table: Locked inputs and outputs. Every later section reproduces these values.")
    parts.append(md_table(["Parameter", "Value"], [
        ["Fabricated Tonnage (take-off, incl. allowances)", f"{tons:,.2f} {tn}s"],
        ["Pricing Unit", ton_definition(est["jurisdiction"])],
        ["Pieces / Line Items", f"{t['pieces']:,} pcs / {t['line_items']:,}"],
        ["Material Type", est["material"]], ["Surface Treatment", est["surface"]],
        ["Assembly Complexity", est["assembly"]],
        ["Material Factor", f"x{f['material']:.2f}"], ["Surface Factor", f"x{f['surface']:.2f}"],
        ["Assembly Factor", f"x{f['assembly']:.2f}"], ["Composite Adjustment Factor", f"x{f['composite']:.4f}"],
        ["Rate Basis", est["rate_basis"]],
        [f"Rate — Low ({cur} per {tn})", m(est["rates"]["low"])],
        [f"Rate — Mid ({cur} per {tn})", m(est["rates"]["mid"])],
        [f"Rate — High ({cur} per {tn})", m(est["rates"]["high"])],
        ["Adjusted Rate — Low", m(adj["low"])], ["Adjusted Rate — Mid", m(adj["mid"])],
        ["Adjusted Rate — High", m(adj["high"])],
        ["Country", est["country"]], ["Tax Rate", f"{est['tax_pct']:g}%"],
        ["**LOCK COST — LOW**", f"**{m(lock['low'])}**"], ["**LOCK COST — MID**", f"**{m(lock['mid'])}**"],
        ["**LOCK COST — HIGH**", f"**{m(lock['high'])}**"],
        [f"Cost per {tn} — Mid", m(est["per_ton_mid"])],
    ], [":--", "--:"]))

    parts.append("## 1. EXECUTIVE SUMMARY")
    parts.append(md_table(["Metric", "Value"], [
        ["Project", proj.get("name") or "—"], ["Country", est["country"]],
        ["Total Fabricated Tonnage", f"{tons:,.2f} {tn}s ({t['gross_t']:,.2f} t · {t['gross_lb']:,.0f} lbs)"],
        ["Pieces", f"{t['pieces']:,}"],
        ["Material / Surface / Assembly", f"{est['material']} / {est['surface']} / {est['assembly']}"],
        ["Composite Adjustment Factor", f"x{f['composite']:.4f}"],
        ["Rate Band Applied", f"{m(est['rates']['low'])} — {m(est['rates']['high'])} per {tn} ({est['rate_basis'].lower()})"],
        ["Tax Rate", f"{est['tax_pct']:g}%"],
        ["Estimated Cost — Low (inc. tax)", m(lock["low"])], ["**Estimated Cost — Mid (inc. tax)**", f"**{m(lock['mid'])}**"],
        ["Estimated Cost — High (inc. tax)", m(lock["high"])], [f"Cost per {tn} — Mid", m(est["per_ton_mid"])],
    ], [":--", "--:"]))

    parts.append("## 2. BASIS OF ESTIMATE")
    sheets = takeoff.get("sheets") or []
    lines = ["**Drawings reviewed:**"]
    lines += [f"- {s['sheet']}" + (f" — {s['title']}" if s.get("title") else "") for s in sheets] or ["- —"]
    wm = takeoff["stats"]["weights_by_method"]
    lines += [
        "",
        "**Tonnage method:** Member-by-member take-off from the drawings (Section 3). Each weight is computed as "
        "Qty × Length × published unit weight (rolled shapes from the designation; HSS, pipe, angles and plates "
        f"from section geometry). {t['line_items']:,} line items; weights by designation {wm.get('designation', 0):,}, "
        f"geometry {wm.get('geometry', 0):,}, drawing-stated {wm.get('stated', 0):,}.",
        "",
        "**Composite factor selection:**",
    ]
    for label, key, factor in (("Material", "material_type", f["material"]), ("Surface", "surface_treatment", f["surface"]),
                               ("Assembly", "assembly_complexity", f["assembly"])):
        b = basis.get(key) or {}
        src = "stated in the instructions" if (takeoff.get("user_overrides") or {}).get(key) else (
            f"{b.get('basis')} ({b.get('sheet')})" if b.get("basis") else "standard practice — not specified on the drawings")
        lines.append(f"- {label} factor x{factor:.2f}: {src}.")
    if est["galv_note"]:
        lines.append(f"- {est['galv_note']}")
    lines += [
        "",
        f"**Rate basis:** {est['rate_basis'].title()} — {m(est['rates']['low'])} / {m(est['rates']['mid'])} / "
        f"{m(est['rates']['high'])} per {tn}; the mid rate gives the headline figure.",
        f"**Tax:** {est['tax_pct']:g}% applied for {est['country']}.",
        "",
        "**Scope basis:** Included — all fabricated structural and miscellaneous steel in the take-off "
        "(primary framing, secondary framing, miscellaneous metals, connection material and plates) plus the "
        "tonnage allowances in Table 3D. Excluded — " + ("; ".join(takeoff.get("excluded_scope") or []) or "items listed in Section 8") + ".",
    ]
    parts.append("\n".join(lines))

    parts.append("## 3. MEMBER-BY-MEMBER TONNAGE TAKE-OFF")
    rows = [r for r in takeoff["rows"] if not r["hardware"]]
    groups = (
        ("Table 3A — Primary Structural Members", lambda r: r["role"] == "PRIMARY"),
        ("Table 3B — Secondary and Miscellaneous Members", lambda r: r["role"] in ("SECONDARY", "MISC")),
        ("Table 3C — Connection Material and Plates", lambda r: r["role"] == "CONNECTION"),
    )
    used: set[int] = set()
    for title, pred in groups:
        sel = [r for r in rows if pred(r)]
        used.update(id(r) for r in sel)
        parts.append(f"**{title}** — {len(sel):,} line items, {sum(r['qty'] for r in sel):,} pcs, "
                     f"{fmt(sum(r['weight_kg'] for r in sel))} kg")
        parts.append(_member_table(sel) if sel else "None in the drawing set.")
    other = [r for r in rows if id(r) not in used]
    if other:
        parts.append(f"**Table 3B (cont.) — Other Members** — {len(other):,} line items")
        parts.append(_member_table(other))

    parts.append("**Table 3D — Tonnage Subtotals by Category** (TOTAL equals the manifest tonnage)")
    div = SHORT_TON_KG if est["ton_key"] == "short_tons" else 1000.0
    sub_rows = [[c["category"], f"{c['pieces']:,}", fmt(c["weight_kg"]), fmt(c["weight_lb"], 0),
                 f"{c['weight_kg'] / div:,.3f}"] for c in takeoff["categories"]]
    sub_rows += [[f"{a['label']} ({a['pct']:.1f}%)", "—", fmt(a["kg"]), fmt(a["kg"] * W.KG_TO_LB, 0),
                  f"{a['kg'] / div:,.3f}"] for a in t["allowances"]]
    sub_rows.append(["**TOTAL**", f"**{t['pieces']:,}**", f"**{fmt(t['gross_kg'])}**",
                     f"**{fmt(t['gross_lb'], 0)}**", f"**{tons:,.2f}**"])
    parts.append(md_table(["Category", "Pieces", "Weight (kg)", "Weight (lbs)", f"{tn.split(' (')[0].title()}s"],
                          sub_rows, [":--", "--:", "--:", "--:", "--:"]))

    parts.append("## 4. COST BUILD-UP BY MEMBER CATEGORY")
    cat_rows = []
    total_tons = sum(x for _, x in est["category_tons"]) or 1.0
    totals_cat = {"low": 0.0, "mid": 0.0, "high": 0.0}
    for name, ct in est["category_tons"]:
        costs = {k: ct * adj[k] for k in ("low", "mid", "high")}
        for k in totals_cat:
            totals_cat[k] += costs[k]
        cat_rows.append([name, f"{ct:,.3f}", m(costs["low"]), m(costs["mid"]), m(costs["high"]),
                         f"{ct / total_tons * 100:.1f}%"])
    cat_rows.append(["**SUBTOTAL (before tax)**", f"**{tons:,.2f}**", f"**{m(totals_cat['low'])}**",
                     f"**{m(totals_cat['mid'])}**", f"**{m(totals_cat['high'])}**", "100%"])
    parts.append(f"Adjusted rates applied uniformly: low {m(adj['low'])} · mid {m(adj['mid'])} · high {m(adj['high'])} per {tn}.")
    parts.append(md_table(["Category", f"{tn.split(' (')[0].title()}s", "Cost Low", "Cost Mid", "Cost High", "Share"],
                          cat_rows, [":--", "--:", "--:", "--:", "--:", "--:"]))

    parts.append("## 5. PROCESS COST BREAKDOWN — MIDPOINT SCENARIO")
    pre_tax_mid = lock["mid"] / (1 + est["tax_pct"] / 100.0)
    amounts = [_round100(pre_tax_mid * pct) for _, pct, _ in PROCESS_SPLIT]
    tax_amt = lock["mid"] - pre_tax_mid
    tax_amt_r = _round100(tax_amt) if est["tax_pct"] else 0.0
    drift = lock["mid"] - (sum(amounts) + tax_amt_r)
    amounts[0] += drift  # absorb rounding in the largest line so the TOTAL is exact
    prow = [[label, f"{pct * 100:.0f}%", m(a), note] for (label, pct, note), a in zip(PROCESS_SPLIT, amounts)]
    if est["tax_pct"]:
        prow.append([f"Tax ({est['tax_pct']:g}%)", "—", m(tax_amt_r), est["country"]])
    prow.append(["**TOTAL**", "100%", f"**{m(lock['mid'])}**", "Midpoint, inc. tax"])
    parts.append(md_table(["Process Activity", "Share", f"Amount ({cur})", "Notes"], prow, [":--", "--:", "--:", ":--"]))

    parts.append("## 6. COST WATERFALL")
    parts.append(md_table(["Step", "Calculation", f"Amount ({cur})"], [
        ["Fabricated tonnage", f"{tons:,.2f} {tn}s", "—"],
        ["Adjusted mid rate", f"{m(est['rates']['mid'])} x {f['composite']:.4f}", f"{m(adj['mid'])} per {tn}"],
        ["Base cost at mid rate", f"{tons:,.2f} x {m(adj['mid'])}", m(sub["mid"])],
        [f"Tax at {est['tax_pct']:g}%", "Base cost x tax", f"+{m(sub['mid'] * est['tax_pct'] / 100.0)}"],
        ["Grand Total — Low", f"{m(est['rates']['low'])} base, composite, tax; rounded to 100", m(lock["low"])],
        ["**Grand Total — Mid (headline)**", f"{m(est['rates']['mid'])} base, composite, tax; rounded to 100", f"**{m(lock['mid'])}**"],
        ["Grand Total — High", f"{m(est['rates']['high'])} base, composite, tax; rounded to 100", m(lock["high"])],
    ], [":--", ":--", "--:"]))

    parts.append("## 7. COST CONVERSION SUMMARY")
    parts.append(md_table(["Field", "Value"], [
        ["Fabricated Tonnage", f"{tons:,.2f} {tn}s"], ["Rate Basis", est["rate_basis"]],
        ["Rate — Low / Mid / High", f"{m(est['rates']['low'])} / {m(est['rates']['mid'])} / {m(est['rates']['high'])} per {tn}"],
        ["Composite Adjustment Factor", f"x{f['composite']:.4f}"],
        ["Adjusted Rate — Low / Mid / High", f"{m(adj['low'])} / {m(adj['mid'])} / {m(adj['high'])} per {tn}"],
        ["Country and Tax", f"{est['country']} at {est['tax_pct']:g}%"],
        ["Grand Total — Low (inc. tax)", m(lock["low"])], ["**Grand Total — Mid (inc. tax)**", f"**{m(lock['mid'])}**"],
        ["Grand Total — High (inc. tax)", m(lock["high"])], [f"Cost per {tn} — Mid", m(est["per_ton_mid"])],
    ], [":--", "--:"]))
    parts.append("Consistency: all figures above are computed once from the take-off and reproduced verbatim "
                 "in the manifest, Section 1 and this section.")

    parts.append("## 8. ASSUMPTIONS AND EXCLUSIONS")
    assumptions = [
        "Material grades as called on the drawings: " + (
            "; ".join(f"{g['category'] or 'Steel'} {g['normalized'] or g['callout']}" for g in (takeoff.get("grades") or [])[:8])
            or "jurisdiction defaults (A992 W-shapes, A500 Gr.C HSS, A36 plate)") + ".",
        f"Surface treatment taken as {est['surface']}; assembly complexity {est['assembly']}.",
        f"Composite factor x{f['composite']:.4f} = material x{f['material']:.2f} × surface x{f['surface']:.2f} × assembly x{f['assembly']:.2f}.",
    ]
    for a in t["allowances"]:
        assumptions.append(f"{a['label']}: {a['pct']:.1f}% of net member weight ({fmt(a['kg'])} kg).")
    assumptions.append(f"Pricing unit is the {tn}; tonnage {tons:,.2f} = {t['gross_kg']:,.1f} kg gross.")
    gaps = takeoff["stats"].get("gap_rows", 0)
    if gaps:
        assumptions.append(f"{gaps:,} line item(s) without a dimensioned length or profile on the issued drawings are "
                           "listed in the take-off and carried at zero weight until dimensioned (see the MTO RFI package).")
    assumptions += (takeoff.get("assumptions") or [])[:12]
    excl = [
        "Site erection labour, cranes, man-lifts and rigging.",
        "Engineer-of-Record stamping and delegated connection design.",
        "Open-web steel joists, joist girders and joist bridging (by the joist manufacturer).",
        "Metal deck, reinforcing steel, and items noted by others.",
        "Anchor rods cast in by the concrete contractor; field touch-up after erection.",
        "3D MEP coordination and cross-trade clash detection.",
    ]
    excl += [e for e in (takeoff.get("excluded_scope") or []) if "joist" not in e.lower()]
    parts.append("**Assumptions:**\n" + "\n".join(f"- {a}" for a in assumptions))
    parts.append("**Exclusions:**\n" + "\n".join(f"- {e}" for e in dict.fromkeys(excl)))
    parts.append("**Conditions that produce a revised estimate:**\n" + "\n".join(f"- {x}" for x in (
        "Tonnage change greater than ±5% from this figure.",
        "Change of coating system (for example shop primer to hot-dip galvanized).",
        "Member categories added that are not in the current drawing set.",
        "Profile changes affecting more than 10% of the tonnage.",
    )))

    if quote:
        parts.append("## 9. CLIENT-FACING QUOTATION")
        parts.append("\n\n".join([
            f"We are pleased to submit our quotation for the supply and shop fabrication of the structural and "
            f"miscellaneous steel for {proj.get('name') or 'the project'}, as shown on the drawings listed in Section 2.",
            f"**Fabricated tonnage:** {tons:,.2f} {tn}s ({t['pieces']:,} pieces).",
            f"**Price range:** {m(lock['low'])} to {m(lock['high'])} {cur}, inclusive of {est['tax_pct']:g}% tax; "
            f"our recommended figure is {m(lock['mid'])}.",
            "**Inclusions:** shop fabrication, " + est["surface"].lower() + ", connection material and hardware as detailed.",
            "**Exclusions:** " + "; ".join(e.rstrip(".") for e in excl[:5]) + ".",
            "Fabrication proceeds from engineer-approved shop drawings under our certified quality program "
            "(AISC Certified Fabricator / CWB / AS-NZS 5131 as applicable).",
            "Validity: 30 calendar days from the date of this quotation. Payment terms: to be agreed.",
            "We welcome the opportunity to discuss the schedule and sequencing with your team.",
        ]))

    parts.append("## 10. ESTIMATE SUMMARY")
    top = sorted(takeoff["categories"], key=lambda c: c["weight_kg"], reverse=True)[:2]
    parts.append(" ".join([
        f"The project carries {tons:,.2f} {tn}s of fabricated steel ({t['pieces']:,} pieces) with a midpoint "
        f"estimate of {m(lock['mid'])} including {est['tax_pct']:g}% tax and a composite factor of x{f['composite']:.4f}.",
        ("The largest shares of tonnage are " + " and ".join(
            f"{c['category']} ({c['weight_kg'] / max(t['net_kg'], 1) * 100:.0f}%)" for c in top) + ".") if top else "",
        f"The {est['rate_basis'].lower()} rate band of {m(est['rates']['low'])} to {m(est['rates']['high'])} per {tn} "
        f"gives a range of {m(lock['low'])} to {m(lock['high'])}.",
        "Erection, delegated connection design, joists and items by others are excluded as listed in Section 8.",
        "A revised estimate follows a tonnage change above ±5% or a change of coating system.",
    ]).strip())
    parts.append(f"_Estimate generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} from the consolidated take-off._")
    return "\n\n".join(p for p in parts if p)


def _member_table(rows: list[dict]) -> str:
    return md_table(
        ["#", "Type", "Mark", "Profile", "Qty", "Length (mm)", "Unit Wt (kg/m)", "Est Wt (kg)", "Est Wt (lbs)",
         "Grade", "Source Sheet", "Method"],
        [[r["no"], r["type"], r["mark"], r["profile"], r["qty"], fmt(r["length_mm"], 0) if r["length_mm"] else "—",
          fmt(r["unit_wt_kgm"], 2) if r["unit_wt_kgm"] else "—", fmt(r["weight_kg"]) if r["weight_kg"] else "—",
          fmt(r["weight_lb"], 0) if r["weight_kg"] else "—", r["grade"], r["sheet"],
          (r["method"] or "").title()] for r in rows],
        [":--", ":--", ":--", ":--", "--:", "--:", "--:", "--:", "--:", ":--", ":--", ":--"],
    )
