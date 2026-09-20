"""Offline unit tests for the itemized take-off engine + cleanup helpers.

These need no server, network or Mongo — they exercise the deterministic Python:
unit-weight lookup, the MTO block parser, weight reconciliation, the cost build-up,
confidence banding and the cleanup date logic. Run: `pytest tests/test_estimation_engine.py`.
"""
import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "_uploads_test"))

import estimation.weights as W
from estimation import mto
from estimation.engine import apply_band_to_extracted


# ── weights ──────────────────────────────────────────────────────────────────
def test_unit_weight_lookup_and_normalisation():
    assert W.lookup_unit_weight("W12x26", "USA") == 38.7
    assert W.lookup_unit_weight("w12 x 26", "USA") == 38.7      # case/space normalised
    assert W.lookup_unit_weight("HSS6x6x1/2", "USA") == 42.9
    assert W.lookup_unit_weight("W310x52", "CANADA") == 52.0
    assert W.lookup_unit_weight("310UB40.4", "AUSTRALIA") == 40.4  # digit-leading token
    assert W.lookup_unit_weight("75PFC", "AUSTRALIA") == 5.92
    assert W.lookup_unit_weight("DOESNOTEXIST", "USA") is None


def test_weight_math():
    assert W.member_weight_kg(4, 6.096, 38.7) == round(4 * 6.096 * 38.7, 3)
    assert W.plate_weight_kg(10, 300, 2.0) == round(10 * 300 * 2.0 * 0.00785, 3)


# ── parsing + reconciliation ─────────────────────────────────────────────────
SAMPLE = """noise before
<<<META>>>
{"jurisdiction":"USA","confidence":82,"primary_material":"A992","drawings_seen":12,
"assumptions":["Lengths from grid"],
"open_rfis":[{"id":"RFI-MTO-001","priority":"CRITICAL","question":"Confirm B7 length"}],
"notes":"BOM-driven"}
<<<END_META>>>
<<<ROWS>>>
no|type|mark|profile|qty|length_mm|unit_weight_kgm|weight_kg|grade|surface|source_sheet|method|flag
1|W-SHAPE|B1|W12x26|4|6096|38.7|944.0|A992|Primer|S-101|BOM DIRECT|
2|W-SHAPE|C1|W12x26|2|3000|38.7|999999|A992|Primer|S-101|BOM DIRECT|
| --- | --- |
3|PLATE|PL1|PL10x300|5||0|117.75|A36|Primer|S-301|BOM DIRECT|
<<<END_ROWS>>>"""


def test_parse_meta():
    meta = mto.parse_meta(SAMPLE)
    assert meta["jurisdiction"] == "USA"
    assert meta["confidence"] == 82
    assert meta["open_rfis"][0]["id"] == "RFI-MTO-001"


def test_parse_rows_skips_header_and_separator():
    rows = mto.parse_rows(SAMPLE, mto.FAB_COLUMNS)
    assert len(rows) == 3
    assert [r["mark"] for r in rows] == ["B1", "C1", "PL1"]


def test_reconciliation_corrects_bad_arithmetic():
    rows = mto.parse_rows(SAMPLE, mto.FAB_COLUMNS)
    clean, stats = mto._reconcile_fab_rows(rows, "USA")
    # Row 2's absurd stated 999999 kg is recomputed to qty*len*uw and flagged.
    assert abs(clean[1]["weight_kg"] - round(2 * 3.0 * 38.7, 2)) < 0.1
    assert "WEIGHT-RECONCILED" in clean[1]["flag"]
    assert stats["weights_reconciled"] == 1
    # Plate row (no table weight, no length) keeps the stated weight.
    assert clean[2]["weight_kg"] == 117.75


def test_category_summary_sums():
    rows = mto.parse_rows(SAMPLE, mto.FAB_COLUMNS)
    clean, _ = mto._reconcile_fab_rows(rows, "USA")
    cats = mto._category_summary(clean)
    total = sum(c["tons"] for c in cats)
    member_kg = sum(r["weight_kg"] for r in clean)
    assert abs(total - member_kg / 1000.0) < 0.01


# ── engine: rate band + build-up + confidence ────────────────────────────────
def _fab_extracted():
    rows = mto.parse_rows(SAMPLE, mto.FAB_COLUMNS)
    clean, stats = mto._reconcile_fab_rows(rows, "USA")
    cats = mto._category_summary(clean)
    return {
        "tonnage": round(sum(r["weight_kg"] for r in clean) * 1.03 / 1000, 2),
        "members_counted": len(clean), "primary_material": "A992", "jurisdiction": "USA",
        "drawings_seen": 12, "confidence": 82, "line_items": clean, "category_summary": cats,
        "assumptions": ["Lengths from grid"], "open_rfis": [{"id": "RFI-MTO-001", "priority": "CRITICAL", "question": "?"}],
        "reconciliation": stats, "notes": "BOM-driven",
    }


def test_apply_band_fabricator_full_report():
    res = apply_band_to_extracted(role="fabricator", extracted=_fab_extracted(),
                                  rate_low=2400, rate_high=3600, country_code="USA")
    v = res["visible"]
    assert v["confidence"]["label"] in ("High", "Medium", "Low")
    assert len(v["cost_buildup"]) == 7
    assert v["line_items"] and v["category_summary"]
    assert v["final_amount_raw"] > 0
    assert v["grand_range_text"] and v["subtotal_mid"]


def test_apply_band_detailer_full_report():
    extracted = {
        "total_hours": 540.0, "drawings": 120, "connections": 450, "complexity": "High",
        "drawings_seen": 18, "confidence": 65,
        "line_items": [{"no": 1, "task": "Drawings", "qty": 120, "unit": "dwgs",
                        "hours_per_unit": 2.5, "hours": 300, "basis": "x", "flag": ""}],
        "assumptions": ["Bolted primary"], "open_rfis": [],
    }
    res = apply_band_to_extracted(role="detailer", extracted=extracted,
                                  rate_low=18, rate_high=25, country_code="USA")
    v = res["visible"]
    assert v["total_hours"] == 540.0
    assert v["confidence"]["label"] == "Medium"
    assert len(v["cost_buildup"]) == 5
    assert v["line_items"]


# ── cleanup date logic ───────────────────────────────────────────────────────
def test_cleanup_older_than():
    import cleanup
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=72)
    assert cleanup._older_than((now - timedelta(hours=100)).isoformat(), cutoff) is True
    assert cleanup._older_than((now - timedelta(hours=1)).isoformat(), cutoff) is False
    assert cleanup._older_than(None, cutoff) is True       # legacy rows sweepable
    assert cleanup._older_than("not-a-date", cutoff) is True
