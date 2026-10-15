"""Take-off v2: groups, members, factors, deck, count-once, EST-flag RFIs, coverage and the 2B report.

Offline — no server, network or Mongo. Run: `pytest tests/test_takeoff_v2.py`.
"""
import os
import tempfile

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="sm_uploads_"))

import pytest  # noqa: E402

import estimation.weights as W  # noqa: E402
from estimation import takeoff as T  # noqa: E402

HDR = "|".join(T.TAKEOFF_COLUMNS)

B1 = f"""<<<META>>>
{{"jurisdiction":"USA","project":{{"name":"Fire Station 22","address":"1 Main St","owner":"City","architect":"ABC Arch",
 "engineer":"XYZ SE","contractor":"BuildCo"}},
 "sheets":[{{"sheet":"S-101","page":"D1 p.1","title":"FOUNDATION PLAN","status":"READ"}},
           {{"sheet":"S-201","page":"D1 p.2","title":"ROOF FRAMING PLAN","status":"READ"}},
           {{"sheet":"S-301","page":"D1 p.3","title":"BUILDING ELEVATIONS","status":"REFERENCE"}}],
 "connections_itemized":true,
 "deck":[{{"sheet":"S-201","location":"Roof","profile":"1.5B","gauge":"20","area_ft2":4800}}],
 "assemblies":[{{"kind":"stair","mark":"ST1","sheet":"S-501","qty":1,"flights":2,"stair_type":"SWITCHBACK"}},
               {{"kind":"HANDRAIL","mark":"HR1","sheet":"S-501","qty":2,"length_ft":24}}],
 "estimate_inputs":{{"erection_sheets":3,"completeness":{{"qty_counted":true,"connections_detailed":false,
   "grades_finishes":true,"drawings_current":true,"no_missing_info":true,"assumptions_documented":true}},
   "reasons":{{"connections_detailed":"Moment connections at grid 3 not detailed"}}}},
 "reconciliation":[{{"mark":"B2","sheet_1":"S-201","value_1":"W12x26","sheet_2":"S-501","value_2":"W12x30","type":"PROFILE",
   "impact":"Weight","resolution":"Confirm"}}],
 "rfis":[{{"id":"RFI-1","priority":"URGENT","sheet":"S-201","question":"Confirm grade of B1."}}]}}
<<<END_META>>>
<<<ROWS>>>
{HDR}
1|COLUMNS|HSS-SQUARE|COLUMN|PRIMARY|C1|Column grid A/1|HSS6x6x3/8|4|14'-8"|4470|||A500|PRIMER|S-101|FOUNDATION PLAN|GRID-CALC||
2|ROOF|W-SHAPE|BEAM|PRIMARY|B1|Roof beam grid 2, TYP this line|W12x26|3|24'-6"|7468|||A992|PRIMER|S-201|ROOF FRAMING PLAN|DIM||EST-QTY RFI-1
3|ROOF|W-SHAPE|BEAM|PRIMARY|B2|Roof beam grid 3|W12x26|2|20'-0"|6096|||A992|PRIMER|S-201|ROOF FRAMING PLAN|DIM|MOMENT1|
4|COLUMNS|HSS-SQUARE|COLUMN|PRIMARY|C1|Column confirm|HSS6x6x3/8|4|14'-8"|4470|||A500|PRIMER|S-301|ELEVATION 1|DIM||
5||W-SHAPE||PRIMARY|J1|Roof joists|18K4|20|30'-0"|9144|||||S-201||DIM||
6|MISC|PIPE|BOLLARD|MISC|BOL1|Site bollard|Pipe6STD|6|6'-0"|1829|||A53|GALV|C-101|SITE PLAN|SCHEDULE||
7|CONNECTION|PLATE|BASE-PLATE|CONNECTION|BP1|Base plate|PL3/4x12|4|1'-0"|305|||A36||S-101|DETAIL 3|DETAIL||
8|HARDWARE|ANCHOR-ROD|ANCHOR-ROD|HARDWARE|AR1|Anchor rods|3/4" F1554|16||||||||S-101||DETAIL||
9|MISC|ANGLE|LEDGER|MISC|L1|Ledger angle|L4x4x1/2||||||A36||S-201|SECTION 4|SCALED||
10|MISC|MISC|DECK|MISC|D1|Roof deck|1.5B20|1|||||||S-201||DIM||
<<<END_ROWS>>>"""


def _takeoff(register=None):
    batches = [{"batch": 1, "meta": T.parse_meta(B1), "rows": T.parse_rows(B1)}]
    ing = {"register": register or [], "callouts_by_profile": {}}
    return T.consolidate(batches, jurisdiction_hint="USA", ingest=ing)


def _row(t, mark, sheet=None):
    return [r for r in t["rows"] if r["mark"] == mark and (sheet is None or r["sheet"] == sheet)]


def test_rows_carry_groups_members_and_factors():
    t = _takeoff()
    c1 = _row(t, "C1")
    assert len(c1) == 1 and c1[0]["group"] == "COLUMNS" and c1[0]["member"] == "COLUMN"
    assert "HSS" in c1[0]["factors"]                       # tube sections add the HSS factor
    bol = _row(t, "BOL1")[0]
    assert bol["group"] == "MISC" and "GALV" in bol["factors"] and "HSS" in bol["factors"]
    assert _row(t, "B2")[0]["factors"] == ["MOMENT1"]
    j1 = _row(t, "J1")
    assert not j1 and any("joist" in e.lower() for e in t["excluded_scope"])


def test_elevation_recount_is_dropped():
    t = _takeoff()
    assert [r["sheet"] for r in _row(t, "C1")] == ["S-101"]
    assert any(d["type"] == "ELEVATION-RECOUNT" for d in t["duplicates"])


def test_deck_by_area_outside_tonnage():
    t = _takeoff()
    assert not _row(t, "D1")
    assert t["deck"][0]["area_ft2"] == 4800 and t["deck"][0]["area_m2"] == pytest.approx(445.9, abs=0.1)
    assert any("deck" in e.lower() for e in t["excluded_scope"])


def test_est_flags_and_conflicts_get_rfis():
    t = _takeoff()
    ids = {r["id"] for r in t["rfis"]}
    for r in t["rows"]:
        if any(f in r["flags"] for f in T.EST_FLAGS):
            assert r["rfi_ids"] and set(r["rfi_ids"]) <= ids, r["mark"]
    l1 = _row(t, "L1")[0]
    assert "EST-LEN" in l1["flags"] and l1["gap"]
    assert any(r["source"] == "CONFLICT" and "B2" in r["question"] for r in t["rfis"])
    assert t["reconciliation"][0]["rfi"].startswith("RFI-MTO-")
    assert [r["id"] for r in t["rfis"]] == [f"RFI-MTO-{i:03d}" for i in range(1, len(t["rfis"]) + 1)]


def test_sums_agree_within_one_pound():
    t = _takeoff()
    weighed = [r for r in t["rows"] if not r["hardware"]]
    net = t["totals"]["net_kg"]
    for key in ("groups", "categories", "roles"):
        assert sum(c["weight_kg"] for c in t[key]) == pytest.approx(net, abs=0.4536)
    assert sum(r["weight_kg"] for r in weighed) == pytest.approx(net, abs=0.4536)
    tot = t["totals"]
    assert tot["gross_kg"] == pytest.approx(net + sum(a["kg"] for a in tot["allowances"]), abs=0.05)
    assert tot["est_tons"] == pytest.approx(tot["gross_kg"] * W.KG_TO_LB / 2000, abs=0.002)
    assert tot["est_tonnage_unit"] == "tons"


def test_plate_uses_40_8_lb_per_sq_ft_per_inch():
    t = _takeoff()
    bp = _row(t, "BP1")[0]
    assert bp["unit_wt_lbft"] == pytest.approx(0.75 * 12 * 40.8 / 12, abs=0.01)


def test_assemblies_and_estimate_inputs_merged():
    t = _takeoff()
    kinds = {a["kind"] for a in t["assemblies"]}
    assert kinds == {"STAIR", "HANDRAIL"}
    stair = next(a for a in t["assemblies"] if a["kind"] == "STAIR")
    assert stair["flights"] == 2 and stair["stair_type"] == "SWITCHBACK" and stair["group"] == "STAIRS"
    ei = t["estimate_inputs"]
    assert ei["erection_sheets"] == 3 and ei["completeness"]["connections_detailed"] is False
    assert "grid 3" in ei["reasons"]["connections_detailed"]


def test_register_coverage_statuses():
    reg = [
        {"ref": "D1 p.1", "doc_index": 1, "page": 1, "sheet": "S-101", "discipline": "STRUCTURAL", "steel_signal": True},
        {"ref": "D1 p.3", "doc_index": 1, "page": 3, "sheet": "S-301", "discipline": "STRUCTURAL", "steel_signal": True},
        {"ref": "D1 p.4", "doc_index": 1, "page": 4, "sheet": "E-101", "discipline": "ELECTRICAL", "steel_signal": False},
        {"ref": "D1 p.5", "doc_index": 1, "page": 5, "sheet": "A-201", "discipline": "ARCHITECTURAL", "steel_signal": True,
         "callouts": {"HSS4x4x1/4": 2},
         "metals_hits": [{"term": "bollard", "keynote": "05 50 00", "context": "STEEL BOLLARD, SEE 05 50 00"}]},
    ]
    t = _takeoff(reg)
    cov = {e["ref"]: e["coverage"] for e in t["register"]}
    assert cov == {"D1 p.1": "READ", "D1 p.3": "REFERENCE", "D1 p.4": "NO STEEL", "D1 p.5": "UNRESOLVED"}
    assert t["coverage"]["unresolved"] == 1
    assert any(m["resolution"] == "UNRESOLVED" and m["keynote"] == "05 50 00" for m in t["metals_sweep"])


def test_report_2b_structure_and_wording():
    t = _takeoff()
    md = T.render_mto_report(t)
    for heading in ("## PROJECT SNAPSHOT", "## OUTPUT 1", "## OUTPUT 2", "## OUTPUT 3", "## OUTPUT 4", "## OUTPUT 5",
                    "## WEIGHT BASIS & ASSUMPTIONS", "### 2.1 Columns & Posts", "Metal Deck", "Hardware (counted"):
        assert heading in md, heading
    assert "| # | Type | Role | Mark | Description | Profile | Qty | Length (ft-in) | Length (mm) | Unit Wt (kg/m) |" in md
    assert "**Subtotal — Columns & Posts**" in md and "**ESTIMATED TONNAGE**" in md
    assert "Estimated Tonnage" in md and "short ton" not in md.lower()
    assert "Fire Station 22" in md and "18K4" not in md
    assert "2,000 lb" in md


def test_old_sixteen_column_output_still_parses():
    old = """<<<ROWS>>>
no|type|role|mark|profile|qty|raw_length|length_mm|unit_wt_kgm|weight_kg|grade|finish|sheet|detail|method|flag
1|W-SHAPE|PRIMARY|B1|W12x26|4|24'-6"|7468|38.7|1156|A992|PRIMER|S-201|3/S-501|PLAN|
<<<END_ROWS>>>"""
    rows = T.parse_rows(old)
    assert rows[0]["profile"] == "W12x26" and rows[0]["view"] == "3/S-501"
    r = T.compute_row(rows[0], "USA")
    assert r["method"] == "DIM" and r["group"] in ("FLOOR", "ROOF") and r["member"] == "BEAM"
