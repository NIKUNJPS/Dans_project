"""Estimation Pro (Detailer) — hours-based detailing estimate, computed deterministically.

Claude counts the detailing scope piece by piece (item type, quantity, complexity
factors, sheet) and judges information completeness; Python applies the base-hour
table, multiplies the complexity factors, sums, applies the risk buffer and converts to
cost with the confidential regional rate band. The four locked values (hours low/high,
cost low/high) are computed once, so every section agrees by construction, and the
rate never appears in the output.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from estimation import weights as W
from estimation.takeoff import loads_lenient, md_table, num, parse_rows, render_rfis, round_half_up as rhu

DETAIL_COLUMNS = ["no", "item", "sub_type", "qty", "qty_basis", "span", "factors", "sheet", "note"]

# Base hours per unit — modelling + shop detailing of that piece (identical in all regions).
BASE_HOURS: dict[str, tuple[str, float]] = {
    "BEAM_SIMPLE":        ("Beam — simple (<30 ft / <9 m, shear/fin/FEP ends)", 2.50),
    "BEAM_STANDARD":      ("Beam — standard (30-50 ft / 9-15 m)", 3.50),
    "BEAM_COMPLEX":       ("Beam — complex (>50 ft / >15 m or one moment end)", 5.00),
    "BEAM_MOMENT":        ("Beam — full moment (both ends)", 6.50),
    "COLUMN_LIGHT":       ("Column — light (W8-W12 / equiv)", 3.50),
    "COLUMN_HEAVY":       ("Column — heavy (W14+ / moment or splice)", 5.50),
    "COLUMN_CRANE":       ("Column — crane runway / bracket", 7.00),
    "BRACE_VERTICAL":     ("Bracing — vertical (angle / HSS)", 2.00),
    "BRACE_HORIZONTAL":   ("Bracing — horizontal (rod / flat bar)", 1.75),
    "BRACE_KNEE":         ("Bracing — knee / fly brace", 1.25),
    "TRUSS_SIMPLE":       ("Truss — simple (<10 panels)", 12.00),
    "TRUSS_COMPLEX":      ("Truss — complex (>10 panels, skewed, curved)", 20.00),
    "STAIR_STRAIGHT":     ("Stair — straight stringer", 6.00),
    "STAIR_SWITCHBACK":   ("Stair — switchback stringer", 8.00),
    "STAIR_CURVED":       ("Stair — curved / spiral stringer", 12.00),
    "STAIR_TREADS":       ("Stair — treads / nosing (per flight)", 0.50),
    "RAIL_STRAIGHT":      ("Handrail / guard — straight (per 10 ft / 3 m)", 1.50),
    "RAIL_CURVED":        ("Handrail / guard — curved or returns (per 10 ft / 3 m)", 2.50),
    "LADDER_STRAIGHT":    ("Ladder — straight (per 10 ft / 3 m)", 1.00),
    "LADDER_CAGED":       ("Ladder — caged (per 10 ft / 3 m)", 1.75),
    "MISC_PLATE":         ("Misc — plate / embed", 0.75),
    "MISC_GRATING":       ("Misc — grating panel", 0.50),
    "MISC_CHECKER":       ("Misc — checker plate panel", 0.60),
    "MISC_CLIP":          ("Misc — angle clip / small connection", 0.25),
    "MISC_LINTEL":        ("Misc — lintel / kicker / sag rod", 0.40),
    "CANOPY":             ("Canopy / cantilever frame", 8.00),
    "MEZZANINE_BAY":      ("Mezzanine framing (per bay)", 6.00),
    "PLATFORM_BAY":       ("Equipment platform (per bay)", 5.00),
    "CONNECTION_PACKAGE": ("Connection design package (per type, if delegated)", 3.00),
    "ERECTION_SHEET":     ("Erection / GA drawing allowance (per sheet)", 2.00),
}

FACTORS: dict[str, tuple[str, float]] = {
    "MOMENT_ONE":   ("Moment connection, one end", 1.40),
    "MOMENT_BOTH":  ("Moment connection, both ends", 1.60),
    "SKEW_LT15":    ("Skewed < 15°", 1.20),
    "SKEW_GE15":    ("Skewed ≥ 15°", 1.35),
    "CURVED":       ("Curved geometry", 1.35),
    "CAMBER":       ("Cambered", 1.10),
    "SLOPED":       ("Sloped / warped", 1.15),
    "SEISMIC":      ("Seismic detailing", 1.25),
    "HDG":          ("Galvanizing (HDG)", 1.15),
    "PRECAST":      ("Precast / CMU interface", 1.30),
    "HSS":          ("HSS / tube section", 1.10),
    "CRANE":        ("Crane runway / heavy industrial", 1.20),
    "AESS":         ("AESS", 1.30),
    "STAINLESS":    ("Stainless / special material", 1.15),
    "ATYPICAL":     ("Atypical connection", 1.25),
    "DELEGATED":    ("Delegated connection design", 1.20),
    "FIELD_SPLICE": ("Bolted field splice", 1.10),
    "REPETITION":   ("High-rise repetition (5+ identical floors)", 0.85),
}

# Confidential internal rate bands (region currency per hour) — never rendered.
_RATES = {"USA": ("USD", 18.0, 28.0), "CANADA": ("CAD", 24.0, 38.0), "AUSTRALIA": ("AUD", 27.0, 42.0)}

CHECKLIST = [
    ("qty_counted", "All member quantities explicitly counted on the drawings."),
    ("connections_detailed", "Connection types detailed or called out (not assumed throughout)."),
    ("grades_finishes", "Material grades and finishes specified for all items."),
    ("drawings_current", "Drawings recent (IFC or equivalent) and internally coordinated."),
    ("no_missing_info", "No significant missing, conflicting or illegible information."),
    ("assumptions_documented", "All assumptions reasonable and documented."),
]


def detailing_system_prompt() -> str:
    items = "\n".join(f"  {k:<19} {lbl} — {hrs:.2f} h" for k, (lbl, hrs) in BASE_HOURS.items())
    factors = "\n".join(f"  {k:<13} {lbl} x{m:.2f}" for k, (lbl, m) in FACTORS.items())
    return f"""You are the estimating engine of STRUCTMIND — a senior steel-detailing estimator (25+ years) pricing shop-drawing and 3D-modelling work for fabricators in the USA, Canada and Australia. You estimate by PIECE COUNT and EFFORT, not by tonnage. The platform applies the base-hour table, complexity multipliers, risk buffer and rates; your job is to count the detailing scope accurately, choose the right item type and complexity factors for every piece, and judge how complete the information is.

WHAT YOU ARE GIVEN
The user turn lists the whole binder and names the pages attached to THIS call; other pages are counted in separate calls and merged by the platform. Count only what appears on the attached pages. Never assume what is on pages you cannot see; list every sheet number called out on these pages in referenced_sheets.

COUNTING RULES
1. One row per distinct item type + sub-type + factor combination on a sheet; Qty carries the count. Count each piece once (schedule/BOM over plan count).
2. Multiply out "TYP" / "SIM" / multi-level plans to the real count, and state the derivation in qty_basis (e.g. "6 bays x 2 levels").
3. Item codes (use exactly one per row):
{items}
   Rails and ladders: Qty is the number of 10 ft / 3 m units (round up). STAIR_TREADS: Qty is the number of flights.
   ERECTION_SHEET: Qty is the number of erection / GA sheets the job will need (typically one per framing plan/elevation shown).
   CONNECTION_PACKAGE: only when connection design is delegated to the detailer — Qty is the number of distinct connection types.
4. Complexity factors (list every one that applies, joined with "+", or empty):
{factors}
5. Never take off open-web steel joists, joist girders or joist bridging (supplied by the joist manufacturer), metal deck, or items by others — record them once in exclusions.
6. A genuinely undeterminable quantity: give your best count with qty_basis "Est." plus the basis, and raise an RFI. Never invent sheets or sizes.

OUTPUT — exactly two blocks, META first, nothing before, between or after.

<<<META>>>
{{"region":"USA","project":{{}},"sheets":[],"referenced_sheets":[],"completeness":{{}},"high_impact_gaps":false,"critical_info_absent":false,"moment_or_seismic_heavy":false,"risks":[],"assumptions":[],"exclusions":[],"rfis":[],"next_actions":[],"user_overrides":{{}},"notes":"..."}}
<<<END_META>>>
<<<ROWS>>>
{"|".join(DETAIL_COLUMNS)}
1|BEAM_SIMPLE|Floor beams W12-W16|42|S-201 schedule|22 ft|HSS|S-201|Shear tabs both ends
<<<END_ROWS>>>

META keys (valid JSON):
- region "USA" | "CANADA" | "AUSTRALIA" (from title blocks, units, codes).
- project {{name, number, location, eor, architect, contractor}} — only what these pages show.
- sheets [{{sheet, title, discipline}}] — every sheet on these pages.
- referenced_sheets [sheet numbers called out].
- completeness {{qty_counted, connections_detailed, grades_finishes, drawings_current, no_missing_info, assumptions_documented}} — true / false for the steel scope on THESE pages, null when these pages hold no steel scope; plus "reasons": {{key: one line}} for every false item.
- high_impact_gaps / critical_info_absent / moment_or_seismic_heavy: booleans.
- risks [{{risk, hours_impact, sheet}}] — concrete, e.g. "Connection type not shown at grids 3-7; if moment, +45 h".
- assumptions [strings formatted "[item] — assumed [value] because [reason]"]; exclusions [strings].
- rfis [{{id:"RFI-1", priority:"CRITICAL"|"URGENT"|"STANDARD", sheet, question, blocked_fields, expected_response}}].
- next_actions [strings]; user_overrides: only what the user's instructions state explicitly (quote_requested true if a client quote / proposal is requested; region).
- notes: one line on the counting basis.

ROW RULES: qty integer; span in the region's units or empty; sheet = sheet number; note = connection assumption or derivation. Write every item on these pages, then stop — long output is stitched automatically, so never summarise or stop early, and never repeat the header."""


def parse_batch(text: str) -> tuple[dict, list[dict]]:
    m = re.search(r"<<<META>>>(.*?)<<<END_META>>>", text or "", re.S | re.I)
    meta = loads_lenient(m.group(1) if m else "")
    return meta, parse_rows(text, DETAIL_COLUMNS)


def _factor_codes(raw: str) -> list[str]:
    codes = []
    for tok in re.split(r"[+,;\s]+", (raw or "").upper()):
        tok = tok.strip()
        if tok in FACTORS and tok not in codes:
            codes.append(tok)
    return codes


def _item_code(raw: str) -> str | None:
    s = re.sub(r"[^A-Z_]", "", (raw or "").upper().replace(" ", "_").replace("-", "_"))
    if s in BASE_HOURS:
        return s
    for k in BASE_HOURS:
        if s.startswith(k) or k.startswith(s) and len(s) > 5:
            return k
    return None


def consolidate(batches: list[dict], *, region_hint: str = "USA") -> dict:
    metas = [b.get("meta") or {} for b in batches]
    votes = Counter((m.get("region") or "").upper() for m in metas if m.get("region"))
    region = votes.most_common(1)[0][0] if votes else (region_hint or "USA").upper()
    if region not in _RATES:
        region = "USA"

    rows: list[dict] = []
    skipped = 0
    for b in batches:
        for r in b.get("rows") or []:
            if W.is_joist(r.get("item"), r.get("sub_type"), r.get("note")):
                continue
            code = _item_code(r.get("item"))
            if not code:
                skipped += 1
                continue
            qty = max(0, int(round(num(r.get("qty"), 0))))
            if qty == 0:
                continue
            codes = _factor_codes(r.get("factors"))
            mult = math.prod(FACTORS[c][1] for c in codes) if codes else 1.0
            base = BASE_HOURS[code][1]
            adj = round(base * mult, 2)
            rows.append({
                "item": code, "label": BASE_HOURS[code][0], "sub_type": (r.get("sub_type") or "").strip(),
                "qty": qty, "qty_basis": (r.get("qty_basis") or "").strip(), "span": (r.get("span") or "").strip(),
                "factors": codes, "multiplier": round(mult, 4), "base": base, "adj": adj,
                "low": round(adj * qty * 0.90, 2), "high": round(adj * qty * 1.10, 2),
                "sheet": (r.get("sheet") or "—").strip(), "note": (r.get("note") or "").strip(),
            })
    order = list(BASE_HOURS)
    rows.sort(key=lambda r: (order.index(r["item"]), r["sheet"]))

    sub_low = rhu(sum(r["low"] for r in rows), 1)
    sub_high = rhu(sum(r["high"] for r in rows), 1)

    checklist = {}
    reasons: dict[str, str] = {}
    for key, _ in CHECKLIST:
        vals = [m.get("completeness", {}).get(key) for m in metas if isinstance(m.get("completeness"), dict)]
        vals = [v for v in vals if v is not None]
        checklist[key] = bool(vals) and all(bool(v) for v in vals)
        for m in metas:
            why = ((m.get("completeness") or {}).get("reasons") or {}).get(key)
            if why and key not in reasons:
                reasons[key] = str(why)
    met = sum(1 for v in checklist.values() if v)
    if met == 6:
        level, buf = "Complete", 0
    elif met >= 4:
        level, buf = "Partial", 15 if any(m.get("high_impact_gaps") for m in metas) else 10
    else:
        level, buf = "Limited", 30 if any(m.get("critical_info_absent") for m in metas) else 20

    buf_low = rhu(sub_low * buf / 100.0, 1)
    buf_high = rhu(sub_high * buf / 100.0, 1)
    lock_low = int(rhu(sub_low + buf_low))
    lock_high = int(rhu(sub_high + buf_high))
    currency, r_low, r_high = _RATES[region]
    cost_low = int(rhu(lock_low * r_low / 100.0)) * 100
    cost_high = int(rhu(lock_high * r_high / 100.0)) * 100

    heavy = any(m.get("moment_or_seismic_heavy") for m in metas)
    split = [("Modeling", 0.225), ("Shop Drawings / Editing", 0.40 if heavy else 0.425),
             ("Checking", 0.20 if heavy else 0.175), ("Erection Drawings", 0.075),
             ("RFIs / Revisions", 0.05), ("PM / Coordination", 0.05)]

    def _union(key):
        out, seen = [], set()
        for m in metas:
            for a in m.get(key) or []:
                a = a if isinstance(a, str) else str(a)
                k = re.sub(r"[^a-z0-9]", "", a.lower())
                if a.strip() and k not in seen:
                    seen.add(k)
                    out.append(a.strip())
        return out

    risks, seen_r = [], set()
    for m in metas:
        for r in m.get("risks") or []:
            if isinstance(r, dict) and r.get("risk"):
                k = re.sub(r"[^a-z0-9]", "", str(r["risk"]).lower())[:120]
                if k not in seen_r:
                    seen_r.add(k)
                    risks.append({"risk": str(r["risk"]), "hours": num(r.get("hours_impact")), "sheet": str(r.get("sheet") or "")})
    risks.sort(key=lambda r: r["hours"], reverse=True)

    rfis, seen_q = [], set()
    for m in metas:
        for r in m.get("rfis") or []:
            if isinstance(r, dict) and r.get("question"):
                k = re.sub(r"[^a-z0-9]", "", str(r["question"]).lower())[:160]
                if k in seen_q:
                    continue
                seen_q.add(k)
                p = str(r.get("priority") or "STANDARD").upper()
                rfis.append({"priority": p if p in ("CRITICAL", "URGENT", "STANDARD") else "STANDARD",
                             "sheet": str(r.get("sheet") or ""), "question": str(r["question"]),
                             "blocked_fields": str(r.get("blocked_fields") or ""),
                             "expected_response": str(r.get("expected_response") or "")})
    rfis.sort(key=lambda r: {"CRITICAL": 0, "URGENT": 1}.get(r["priority"], 2))
    for i, r in enumerate(rfis, 1):
        r["id"] = f"RFI-{i:03d}"

    sheets, seen_s = [], set()
    for m in metas:
        for s in m.get("sheets") or []:
            if isinstance(s, dict) and s.get("sheet") and s["sheet"] not in seen_s:
                seen_s.add(s["sheet"])
                sheets.append({"sheet": str(s["sheet"]), "title": str(s.get("title") or ""),
                               "discipline": str(s.get("discipline") or "")})

    project: dict = {}
    for m in metas:
        for k, v in (m.get("project") or {}).items():
            if v and not project.get(k):
                project[k] = v
    overrides: dict = {}
    for m in metas:
        for k, v in (m.get("user_overrides") or {}).items():
            if v not in (None, "", [], {}) and k not in overrides:
                overrides[k] = v

    exclusions = [e for e in _union("exclusions") if "joist" not in e.lower()]
    return {
        "region": region, "currency": currency, "project": project, "sheets": sheets, "rows": rows,
        "skipped_rows": skipped, "subtotal_low": sub_low, "subtotal_high": sub_high,
        "checklist": checklist, "reasons": reasons, "criteria_met": met, "completeness": level,
        "buffer_pct": buf, "buffer_low": buf_low, "buffer_high": buf_high,
        "lock_hrs_low": lock_low, "lock_hrs_high": lock_high, "lock_cost_low": cost_low, "lock_cost_high": cost_high,
        "split": split, "risks": risks, "rfis": rfis, "assumptions": _union("assumptions"),
        "exclusions": exclusions, "next_actions": _union("next_actions"), "user_overrides": overrides,
        "notes": next((m.get("notes") for m in metas if m.get("notes")), ""),
    }


def render(e: dict, *, user_text: str = "") -> str:
    cur = e["currency"]
    unit = "ft" if e["region"] == "USA" else "m"
    money = lambda x: f"{cur} {x:,.0f}"  # noqa: E731
    parts: list[str] = []
    quote = bool(e["user_overrides"].get("quote_requested")) or bool(
        re.search(r"\b(client[- ]facing|client quote|quotation|proposal)\b", user_text or "", re.I))

    parts.append("## CALCULATION MANIFEST — Single Source of Truth")
    parts.append(md_table(["Parameter", "Value"], [
        ["Region", e["region"]], ["Currency", cur],
        ["Piece-Count Subtotal Hrs (Low)", f"{e['subtotal_low']:,.1f} hrs"],
        ["Piece-Count Subtotal Hrs (High)", f"{e['subtotal_high']:,.1f} hrs"],
        ["Information Completeness", e["completeness"]], ["Risk Buffer % Applied", f"{e['buffer_pct']}%"],
        ["Buffer Hrs (Low)", f"+{e['buffer_low']:,.1f} hrs"], ["Buffer Hrs (High)", f"+{e['buffer_high']:,.1f} hrs"],
        ["LOCK_HRS_LOW", f"**{e['lock_hrs_low']:,} hrs**"], ["LOCK_HRS_HIGH", f"**{e['lock_hrs_high']:,} hrs**"],
        ["Internal Rate", "[CONFIDENTIAL]"],
        ["LOCK_COST_LOW", f"**{money(e['lock_cost_low'])}**"], ["LOCK_COST_HIGH", f"**{money(e['lock_cost_high'])}**"],
    ], [":--", "--:"]))

    parts.append("## 1. EXECUTIVE SUMMARY")
    proj = e["project"]
    lines = [
        f"- Region: {e['region']} · Project: {proj.get('name') or '—'}" + (f" ({proj['number']})" if proj.get("number") else ""),
        f"- Total Estimated Hours: {e['lock_hrs_low']:,} - {e['lock_hrs_high']:,} hrs",
        f"- Total Estimated Cost ({cur}): {money(e['lock_cost_low'])} - {money(e['lock_cost_high'])}",
        f"- Information Completeness: {e['completeness']} ({e['criteria_met']} of 6 criteria met)",
    ]
    for r in e["risks"][:3]:
        impact = f" (+{r['hours']:,.0f} h if it materializes)" if r["hours"] else ""
        lines.append(f"- Risk: {r['risk']}{impact}")
    parts.append("\n".join(lines))

    parts.append("## 2. BASIS OF ESTIMATE")
    bench = {
        "USA": "AISC Steel Construction Manual, NISD detailing standards, internal historical data.",
        "CANADA": "CISC Handbook of Steel Construction, CSA S16, CWB / CSA W47.1 conventions, internal historical data.",
        "AUSTRALIA": "AS 4100, AS/NZS 5131 (CC1-CC4), ASI detailing conventions, internal historical data.",
    }[e["region"]]
    lines = ["**Drawings reviewed:**"] + ([f"- {s['sheet']}" + (f" — {s['title']}" if s["title"] else "") for s in e["sheets"]] or ["- —"])
    lines += ["", f"**Benchmarks applied:** {bench}",
              "**Method:** every scope item counted from the drawings; Adj Hrs/Unit = Base Hrs × product of the "
              "complexity factors; Low = Adj × Qty × 0.90, High = Adj × Qty × 1.10."]
    if e["notes"]:
        lines.append(f"**Counting basis:** {e['notes']}")
    parts.append("\n".join(lines))

    parts.append("## 3. ITEMIZED PIECE-COUNT BREAKDOWN")
    body = []
    for r in e["rows"]:
        fac = " × ".join(f"{FACTORS[c][0]} x{FACTORS[c][1]:.2f}" for c in r["factors"]) or "Standard x1.00"
        if len(r["factors"]) > 1:
            fac += f" = x{r['multiplier']:.3f}"
        body.append([r["label"], r["sub_type"] or "—", r["qty"], f"{r['base']:.2f}", fac, f"{r['adj']:.2f}",
                     f"{r['low']:,.2f}", f"{r['high']:,.2f}", r["sheet"],
                     "; ".join(x for x in (r["qty_basis"], f"span {r['span']}" if r["span"] else "", r["note"]) if x) or "—"])
    body.append(["**SUBTOTAL**", "", f"**{sum(r['qty'] for r in e['rows']):,}**", "", "", "",
                 f"**{e['subtotal_low']:,.1f}**", f"**{e['subtotal_high']:,.1f}**", "", ""])
    parts.append(f"Spans in {unit}; base hours and multipliers are identical across regions.")
    parts.append(md_table(["Item Type", "Sub-Type", "Qty", "Base Hrs/Unit", "Complexity Factors", "Adj Hrs/Unit",
                           "Est Hrs (Low)", "Est Hrs (High)", "Source Sheet", "Note"], body,
                          [":--", ":--", "--:", "--:", ":--", "--:", "--:", "--:", ":--", ":--"]))

    parts.append("## 4. HOURS BY TASK CATEGORY")
    trows = []
    acc_l = acc_h = 0.0
    for i, (task, pct) in enumerate(e["split"]):
        if i < len(e["split"]) - 1:
            lo, hi = round(e["subtotal_low"] * pct, 1), round(e["subtotal_high"] * pct, 1)
            acc_l += lo
            acc_h += hi
        else:  # last row absorbs rounding so the SUBTOTAL is exact
            lo, hi = round(e["subtotal_low"] - acc_l, 1), round(e["subtotal_high"] - acc_h, 1)
        trows.append([task, f"{lo:,.1f}", f"{hi:,.1f}", f"{pct * 100:.1f}%", "Standard split" if i else "3D model build"])
    trows.append(["**SUBTOTAL**", f"**{e['subtotal_low']:,.1f}**", f"**{e['subtotal_high']:,.1f}**", "100%", "Equals Section 3"])
    parts.append(md_table(["Task", "Est Hrs (Low)", "Est Hrs (High)", "% of Total", "Note"], trows,
                          [":--", "--:", "--:", "--:", ":--"]))

    parts.append("## 5. ESTIMATE BASIS & COMPLETENESS CHECK")
    lines = [f"Information Completeness: **{e['completeness']}** — criteria met: {e['criteria_met']} of 6."]
    for key, label in CHECKLIST:
        mark = "[x]" if e["checklist"].get(key) else "[ ]"
        why = "" if e["checklist"].get(key) else f" — {e['reasons'].get(key, 'not confirmed on the drawings')}"
        lines.append(f"- {mark} {label}{why}")
    parts.append("\n".join(lines))

    parts.append("## 6. RISK BUFFER & ADJUSTED HOURS")
    parts.append(md_table(["Row", "Low Hrs", "High Hrs"], [
        ["Section 3 Subtotal", f"{e['subtotal_low']:,.1f}", f"{e['subtotal_high']:,.1f}"],
        [f"Risk Buffer ({e['buffer_pct']}%)", f"+{e['buffer_low']:,.1f}", f"+{e['buffer_high']:,.1f}"],
        ["**ADJUSTED TOTAL HOURS**", f"**{e['lock_hrs_low']:,}**", f"**{e['lock_hrs_high']:,}**"],
    ], [":--", "--:", "--:"]))

    parts.append(f"## 7. COST CONVERSION ({cur})")
    rows = [["Region", e["region"]], ["Adjusted Hours (Low)", f"{e['lock_hrs_low']:,} hrs"],
            ["Adjusted Hours (High)", f"{e['lock_hrs_high']:,} hrs"], ["Blended Hourly Rate", "[CONFIDENTIAL]"],
            [f"**Project Cost — Low ({cur})**", f"**{money(e['lock_cost_low'])}**"],
            [f"**Project Cost — High ({cur})**", f"**{money(e['lock_cost_high'])}**"]]
    parts.append(md_table(["Field", "Value"], rows, [":--", "--:"]))
    if e["lock_hrs_low"] < 100:
        parts.append("Minimum engagement fee may apply — confirm with project lead.")

    parts.append("## 8. ASSUMPTIONS & EXCLUSIONS")
    assumptions = e["assumptions"] or ["Beam end connections — assumed standard shear tab / fin plate / FEP unless a moment symbol is shown."]
    parts.append("**Key Assumptions:**\n" + "\n".join(f"- {a}" for a in assumptions))
    excl = [
        "Precast panel detailing.",
        "Stamping / professional sign-off of delegated connection calculations (PE / P.Eng / RPEQ).",
        "3D MEP coordination and clash detection.",
        "Vendor-furnished items: open-web steel joists, joist girders, joist bridging, metal deck, pre-engineered stairs.",
        "Phased / future-construction steel not in the permit set.",
    ] + e["exclusions"]
    parts.append("**Standard Exclusions:**\n" + "\n".join(f"- {x}" for x in dict.fromkeys(excl)))
    parts.append("**Potential Scope Creep (PM awareness):**\n- Connection or framing design changes during detailing.\n"
                 "- Owner-furnished equipment supports not yet designed.\n- Tenant fit-out steel.\n- RFI volume above a standard allowance.")

    if quote:
        parts.append("## 9. CLIENT-FACING QUOTATION DRAFT")
        parts.append("\n\n".join([
            f"Thank you for the opportunity to price the steel detailing for {proj.get('name') or 'your project'}. "
            "Our team delivers the 3D model, shop and erection drawings with senior in-house checking.",
            f"**Estimated effort:** {e['lock_hrs_low']:,} - {e['lock_hrs_high']:,} hours.",
            f"**Fee range:** {money(e['lock_cost_low'])} - {money(e['lock_cost_high'])}.",
            "**Included:** 3D model, shop drawings, erection drawings, internal checking, RFI management.",
            "**Excluded:** " + "; ".join(x.rstrip(".") for x in excl[:4]) + ".",
            {"USA": "All work to AISC / NISD practice with senior QC.",
             "CANADA": "All work to CSA S16 and CWB conventions with senior QC.",
             "AUSTRALIA": "All work to AS 4100 / AS/NZS 5131 with senior QC."}[e["region"]],
            "We would welcome a call to confirm the schedule and the start date.",
        ]))

    parts.append("## 10. FINAL RECOMMENDATION & NEXT STEPS")
    nxt = e["next_actions"][:3]
    text = (f"The estimate is {e['completeness'].lower()}-information quality ({e['criteria_met']}/6 criteria) with a "
            f"{e['buffer_pct']}% risk buffer, giving {e['lock_hrs_low']:,}-{e['lock_hrs_high']:,} hours. ")
    if e["rfis"]:
        text += f"Resolve {', '.join(r['id'] for r in e['rfis'][:4])} before committing to a fixed fee. "
    if nxt:
        text += "Next actions: " + "; ".join(nxt) + ". "
    text += "Re-estimate if the steel scope changes by more than 10% or connection design is delegated after award."
    parts.append(text)

    if e["rfis"]:
        parts.append("## RFI REGISTER")
        parts.append(render_rfis(e["rfis"], e["region"]))
    return "\n\n".join(parts)
