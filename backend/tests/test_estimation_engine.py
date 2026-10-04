"""Offline unit tests for the deterministic take-off, estimates and cleanup helpers.

No server, network or Mongo needed. Run: `pytest tests/test_estimation_engine.py`.
"""
import os
import tempfile

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="sm_uploads_"))

import pytest

import estimation.weights as W
from estimation import detailing_estimate, fab_estimate
from estimation.ai_extract import takeoff_to_extracted
from estimation.engine import apply_band_to_extracted
from estimation.takeoff import consolidate, parse_meta, parse_rows, render_mto_report

LBFT = W.LBFT_TO_KGM


# ── unit weights: designation and geometry vs published tables ───────────────
@pytest.mark.parametrize("profile,jur,published,unit", [
    ("W12x26", "USA", 26.0, "lbft"),
    ("W 14 x 48 A992", "USA", 48.0, "lbft"),
    ("HSS8x8x1/2", "USA", 48.85, "lbft"),          # AISC Table 1-12
    ("HSS4x4x1/4", "USA", 12.21, "lbft"),
    ("HSS 8 x 4 x 1/4", "USA", 19.02, "lbft"),
    ("HSS6.625x0.280", "USA", 18.97, "lbft"),
    ("Pipe 4 STD", "USA", 10.79, "lbft"),
    ("PIPE6XS", "USA", 28.57, "lbft"),
    ("L4x4x1/2", "USA", 12.8, "lbft"),
    ("2L4x4x3/8", "USA", 19.6, "lbft"),
    ("C10x15.3", "USA", 15.3, "lbft"),
    ("W310x52", "CANADA", 52.0, "kgm"),
    ("HSS127x127x6.4", "CANADA", 23.4, "kgm"),
    ("310UB46.2", "AUSTRALIA", 46.2, "kgm"),
    ("SHS100x100x6", "AUSTRALIA", 17.0, "kgm"),
    ("EA100x100x8", "AUSTRALIA", 12.2, "kgm"),
    ("150PFC", "AUSTRALIA", 17.7, "kgm"),
])
def test_unit_weight_matches_published_tables(profile, jur, published, unit):
    uw = W.compute_unit_weight(profile, jur)
    assert uw is not None
    got = uw.kg_per_m / LBFT if unit == "lbft" else uw.kg_per_m
    assert abs(got - published) / published < 0.015


def test_plate_weight_per_metre_and_unknowns():
    assert abs(W.lookup_unit_weight("PL1/2x8", "USA") - 0.5 * 8 * W.LB_PER_FT_PER_IN2 * LBFT) < 0.01
    assert abs(W.lookup_unit_weight("PL12x200", "CANADA") - 12 * 200 * 0.00785) < 0.01
    assert W.compute_unit_weight("GRATING 19-W-4", "USA") is None
    assert W.compute_unit_weight("", "USA") is None


@pytest.mark.parametrize("raw,mm", [
    ("24'-6\"", 7467.6), ("24'-6 3/8\"", 7477.1), ("24' 6-3/8\"", 7477.1), ("24'", 7315.2),
    ("290\"", 7366.0), ("3/4\"", 19.0), ("7,468 mm", 7468.0), ("7.468 m", 7468.0),
    ("20'-0\" [Grid A-B]", 6096.0),
])
def test_length_parsing(raw, mm):
    assert W.parse_length_mm(raw) == pytest.approx(mm, abs=0.1)


def test_length_parsing_gaps():
    assert W.parse_length_mm("VIF") is None
    assert W.parse_length_mm("SEE PLAN") is None
    assert W.parse_length_mm("—") is None


@pytest.mark.parametrize("token,expected", [
    ("18K4", True), ("24LH06", True), ("48G8N10.5K", True), ("Roof joist", True),
    ("OWSJ 400", True), ("W12x26", False), ("L2x2x1/4", False), ("HSS6x6x3/8", False),
])
def test_joist_detection(token, expected):
    assert W.is_joist(token) is expected


# ── take-off parsing + consolidation ────────────────────────────────────────
B1 = """noise
<<<META>>>
{"jurisdiction":"USA","sheets":[{"sheet":"S-201","title":"2nd Floor Framing"}],
 "referenced_sheets":["S-501","S-999"],"connections_itemized":false,
 "rfis":[{"id":"RFI-1","priority":"URGENT","sheet":"S-201","question":"Confirm grade of B3."}],}
<<<END_META>>>
<<<ROWS>>>
no|type|role|mark|profile|qty|raw_length|length_mm|unit_wt_kgm|weight_kg|grade|finish|sheet|detail|method|flag
1|W-SHAPE|PRIMARY|B1|W12x26|4|24'-6"|7468|38.7|1156|A992|PRIMER|S-201||PLAN|
2|W-SHAPE|PRIMARY|B3|W16x31|2|30'-0"|9144|46.1|843|A992|PRIMER|S-201||PLAN|RFI-1
| --- | --- |
3|JOIST|SECONDARY|J1|18K4|20|30'-0"|9144|||||S-201||PLAN|
4|HSS-SQUARE|PRIMARY|C1|HSS8x8x1/2|6|14'-0"|4267|58.5|1498|A500|PRIMER|S-201||PLAN|
<<<END_ROWS>>>"""

B2 = """<<<META>>>
{"jurisdiction":"USA","sheets":[{"sheet":"S-501","title":"Beam Schedule"}],
 "rfis":[{"id":"RFI-1","priority":"CRITICAL","sheet":"S-501","question":"Length of B9 not shown."}]}
<<<END_META>>>
<<<ROWS>>>
no|type|role|mark|profile|qty|raw_length|length_mm|unit_wt_kgm|weight_kg|grade|finish|sheet|detail|method|flag
1|W-SHAPE|PRIMARY|B1|W12x26|4|24'-6"|7468|38.7|1156|A992|PRIMER|S-501||SCHEDULE|
2|W-SHAPE|PRIMARY|B9|W10x22|3||||||A992||S-501||SCHEDULE|RFI-1
3|PLATE|CONNECTION|BP1|PL3/4x12|6|1'-0"|305|||A36||S-501||DETAIL|
4|ANCHOR-ROD|HARDWARE|AR1|3/4" F1554|24|||||F1554||S-501||DETAIL|COUNT-ONLY
<<<END_ROWS>>>"""


def _takeoff():
    batches = [{"batch": i, "meta": parse_meta(t), "rows": parse_rows(t)} for i, t in ((1, B1), (2, B2))]
    return consolidate(batches, jurisdiction_hint="USA")


def test_parse_meta_tolerates_trailing_commas_and_noise():
    meta = parse_meta(B1)
    assert meta["jurisdiction"] == "USA"
    assert meta["rfis"][0]["id"] == "RFI-1"


def test_parse_rows_skips_header_and_separator():
    rows = parse_rows(B1)
    assert [r["mark"] for r in rows] == ["B1", "B3", "J1", "C1"]


def test_joists_excluded_and_listed_once():
    t = _takeoff()
    assert all("18K4" not in r["profile"] for r in t["rows"])
    assert t["stats"]["joists_excluded"] == 20
    assert sum("joist" in e.lower() for e in t["excluded_scope"]) == 1


def test_plan_count_not_added_when_schedule_has_same_pieces():
    t = _takeoff()
    b1_rows = [r for r in t["rows"] if r["mark"] == "B1"]
    assert len(b1_rows) == 1 and b1_rows[0]["method"] == "SCHEDULE"
    assert any(r["type"] == "BOM-VS-DRAWING" for r in t["reconciliation"])
    assert t["stats"]["double_counts_removed"] == 1


def test_weights_recomputed_and_member_count_is_sum_of_qty():
    t = _takeoff()
    c1 = next(r for r in t["rows"] if r["mark"] == "C1")
    expected = 6 * 4.2672 * W.compute_unit_weight("HSS8x8x1/2").kg_per_m
    assert c1["weight_kg"] == pytest.approx(expected, rel=1e-3)
    assert c1["weight_reconciled"] is True          # model's 1,498 kg used the wrong table
    weighed = [r for r in t["rows"] if not r["hardware"]]
    assert t["totals"]["pieces"] == sum(r["qty"] for r in weighed)
    assert sum(c["pieces"] for c in t["categories"]) == t["totals"]["pieces"]
    assert t["totals"]["net_kg"] == pytest.approx(sum(r["weight_kg"] for r in weighed), abs=0.05)
    assert sum(c["weight_kg"] for c in t["categories"]) == pytest.approx(t["totals"]["net_kg"], abs=0.5)


def test_allowances_and_gross_tonnage():
    t = _takeoff()
    tot = t["totals"]
    assert tot["gross_kg"] == pytest.approx(tot["net_kg"] + sum(a["kg"] for a in tot["allowances"]), abs=0.05)
    assert tot["gross_short_tons"] == pytest.approx(tot["gross_kg"] / 907.18474, abs=0.001)
    # connection material (6 base plates) is itemized but < 2% of the weight and the
    # batch says connections are not itemized → connection allowance applies.
    assert len(tot["allowances"]) in (1, 2)


def test_rfis_renumbered_globally_and_flags_follow():
    t = _takeoff()
    ids = [r["id"] for r in t["rfis"]]
    assert ids == [f"RFI-MTO-{i:03d}" for i in range(1, len(ids) + 1)]
    assert t["rfis"][0]["priority"] == "CRITICAL"
    b9 = next(r for r in t["rows"] if r["mark"] == "B9")
    assert b9["gap"] and "RFI-MTO-" in b9["flag"]
    b3 = next(r for r in t["rows"] if r["mark"] == "B3")
    linked = next(r for r in t["rfis"] if r["id"] in b3["flag"])
    assert "grade of B3" in linked["question"]


def test_hardware_counted_not_weighed():
    t = _takeoff()
    ar = next(r for r in t["rows"] if r["mark"] == "AR1")
    assert ar["hardware"] and ar["weight_kg"] == 0
    assert t["hardware"][0]["qty"] == 24


def test_referenced_missing_sheets():
    t = _takeoff()
    assert t["referenced_missing"] == ["S-999"]


def test_mto_report_has_all_outputs_and_consistent_totals():
    t = _takeoff()
    md = render_mto_report(t)
    for heading in ("OUTPUT 1", "OUTPUT 2", "OUTPUT 3", "OUTPUT 4", "OUTPUT 5"):
        assert f"## {heading}" in md
    assert "18K4" not in md
    assert f"{t['totals']['pieces']:,}" in md
    assert "TOTAL FABRICATED WEIGHT" in md


# ── fabricator estimate: every section agrees ───────────────────────────────
def test_fabricator_estimate_locked_values_consistent():
    t = _takeoff()
    est = fab_estimate.compute(t)
    assert est["ton_name"] == "short ton"
    assert est["tons_exact"] == pytest.approx(t["totals"]["gross_short_tons"])
    assert est["lock"]["low"] < est["lock"]["mid"] < est["lock"]["high"]
    assert est["lock"]["mid"] % 100 == 0
    md = fab_estimate.render(t, est)
    mid = f"${est['lock']['mid']:,.0f}"
    assert md.count(mid) >= 4                 # manifest, summary, process total, conversion
    assert "Open-web steel joists" in md


def test_fabricator_estimate_user_rates_and_galvanizing_once():
    t = _takeoff()
    t["user_overrides"] = {"rate_low": 3000, "rate_high": 5000, "material_type": "Galvanised",
                           "surface_treatment": "hot dip galvanized"}
    est = fab_estimate.compute(t)
    assert est["rate_basis"] == "USER-PROVIDED"
    assert est["rates"]["mid"] == 4000
    assert est["factors"]["material"] == 1.0 and est["factors"]["surface"] == 1.28


def test_metric_jurisdiction_prices_per_tonne():
    t = _takeoff()
    t["jurisdiction"] = "CANADA"
    est = fab_estimate.compute(t)
    assert est["ton_name"] == "tonne" and est["currency"] == "CAD"
    assert est["tons_exact"] == pytest.approx(t["totals"]["gross_t"])


# ── detailer estimate: hours math ───────────────────────────────────────────
DET = """<<<META>>>
{"region":"USA","sheets":[{"sheet":"S-201","title":"Framing"}],
 "completeness":{"qty_counted":true,"connections_detailed":false,"grades_finishes":true,
   "drawings_current":true,"no_missing_info":true,"assumptions_documented":true,
   "reasons":{"connections_detailed":"Moment connections at grid 3 not detailed"}},
 "risks":[{"risk":"Connections at grid 3 unknown","hours_impact":40,"sheet":"S-201"}]}
<<<END_META>>>
<<<ROWS>>>
no|item|sub_type|qty|qty_basis|span|factors|sheet|note
1|BEAM_SIMPLE|Floor beams|40|schedule|22 ft||S-201|
2|BEAM_COMPLEX|Moment girders|4|plan|52 ft|MOMENT_ONE+HSS|S-201|
3|JOIST|Roof joists|30|plan|||S-201|
<<<END_ROWS>>>"""


def test_detailing_estimate_hours_and_buffer():
    meta, rows = detailing_estimate.parse_batch(DET)
    e = detailing_estimate.consolidate([{"batch": 1, "meta": meta, "rows": rows}])
    assert len(e["rows"]) == 2                     # joist row dropped
    beam = 2.5 * 40
    girder = 5.0 * 1.4 * 1.1 * 4
    assert e["subtotal_low"] == pytest.approx((beam + girder) * 0.9, abs=0.2)
    assert e["subtotal_high"] == pytest.approx((beam + girder) * 1.1, abs=0.2)
    assert e["criteria_met"] == 5 and e["completeness"] == "Partial" and e["buffer_pct"] == 10
    # Prompt spec: buffer rounded to 0.1 h, locked hours to the hour, cost to the 100 (half-up).
    buf_low = round(e["subtotal_low"] * 0.10 + 1e-9, 1)
    assert e["buffer_low"] == pytest.approx(buf_low, abs=0.051)
    assert e["lock_hrs_low"] == int(e["subtotal_low"] + e["buffer_low"] + 0.5)
    assert e["lock_cost_low"] == int(e["lock_hrs_low"] * 18 / 100 + 0.5) * 100
    md = detailing_estimate.render(e)
    assert "[CONFIDENTIAL]" in md


def test_round_half_up_is_commercial():
    from estimation.takeoff import round_half_up
    assert round_half_up(156.5) == 157 and round_half_up(129.5) == 130 and round_half_up(0.125, 2) == 0.13


def test_detailing_estimate_never_shows_rate():
    meta, rows = detailing_estimate.parse_batch(DET)
    e = detailing_estimate.consolidate([{"batch": 1, "meta": meta, "rows": rows}])
    md = detailing_estimate.render(e)
    assert "/hr" not in md and "per hour" not in md.lower()


# ── estimate page adapter + rate band ───────────────────────────────────────
def test_apply_band_fabricator_prices_short_tons_in_usa():
    t = _takeoff()
    ex = takeoff_to_extracted(t)
    res = apply_band_to_extracted(role="fabricator", extracted=ex, rate_low=3000, rate_high=4000, country_code="USA")
    v = res["visible"]
    assert v["extracted"]["ton_unit"] == "short ton"
    assert v["extracted"]["tonnage"] == pytest.approx(round(t["totals"]["gross_short_tons"], 2), abs=0.01)
    assert len(v["cost_buildup"]) == 7 and v["final_amount_raw"] > 0


def test_apply_band_detailer_full_report():
    extracted = {
        "total_hours": 540.0, "drawings": 120, "connections": 450, "complexity": "High",
        "drawings_seen": 18, "confidence": 65,
        "line_items": [{"no": 1, "task": "Drawings", "qty": 120, "unit": "dwgs",
                        "hours_per_unit": 2.5, "hours": 300, "basis": "x", "flag": ""}],
        "assumptions": ["Bolted primary"], "open_rfis": [],
    }
    res = apply_band_to_extracted(role="detailer", extracted=extracted, rate_low=18, rate_high=25, country_code="USA")
    v = res["visible"]
    assert v["total_hours"] == 540.0
    assert v["confidence"]["label"] == "Medium"
    assert len(v["cost_buildup"]) == 5


# ── cleanup date logic ───────────────────────────────────────────────────────
def test_cleanup_older_than():
    import cleanup
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=72)
    assert cleanup._older_than((now - timedelta(hours=100)).isoformat(), cutoff) is True
    assert cleanup._older_than((now - timedelta(hours=1)).isoformat(), cutoff) is False
    assert cleanup._older_than(None, cutoff) is True
    assert cleanup._older_than("not-a-date", cutoff) is True
