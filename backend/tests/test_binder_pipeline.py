"""Offline tests for binder batching and the one-report consolidation pipeline.

Claude is stubbed, so these exercise only the deterministic Python: page splitting,
batch packing, bulk-table merging, RFI renumbering and the mode dispatch.
"""
import asyncio
import os
import tempfile
import re

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="sm_uploads_"))

import pytest

pymupdf = pytest.importorskip("pymupdf")
pypdf = pytest.importorskip("pypdf")

import pipeline  # noqa: E402
from binder import plan_binder  # noqa: E402
from claude_engine import BatchOutput, GenResult, Usage, _stitch  # noqa: E402


def _pdf(path, pages):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=600, height=400).insert_text((40, 40), f"S-{i + 1:03d}")
    doc.save(str(path))
    return str(path)


def test_binder_splits_by_page_and_keeps_order(tmp_path):
    a = _pdf(tmp_path / "a.pdf", 61)
    b = _pdf(tmp_path / "b.pdf", 10)
    plan = plan_binder([(a, "application/pdf", "S-Structural.pdf"), (b, "application/pdf", "A-Arch.pdf")],
                       max_pages=25, scratch_root=str(tmp_path))
    try:
        pages = [b.pages for b in plan.batches]
        assert sum(pages) == 71 and max(pages) <= 25
        titles = [u.title for bt in plan.batches for u in bt.units]
        assert titles[0].startswith("D1 S-Structural.pdf — pages 1-")
        assert titles[-1] == "D2 A-Arch.pdf — all 10 pages"
        counts = [len(pypdf.PdfReader(u.path).pages) for bt in plan.batches for u in bt.units]
        assert counts == [u.pages for bt in plan.batches for u in bt.units]
        assert "BINDER MANIFEST — 2 document(s), 71 page(s)" in plan.manifest_text()
    finally:
        plan.cleanup()
    assert not os.path.exists(plan.temp_dir or "/nonexistent")


def test_small_binder_is_single_call(tmp_path):
    a = _pdf(tmp_path / "a.pdf", 8)
    plan = plan_binder([(a, "application/pdf", "Set.pdf")], max_pages=25, scratch_root=str(tmp_path))
    try:
        assert plan.is_single_call and plan.batches[0].units[0].path == a   # unsplit original
    finally:
        plan.cleanup()


def test_large_text_files_are_split_into_parts(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_text("mark,profile\n" + "".join(f"B{i},W12x26\n" for i in range(40000)))
    plan = plan_binder([(str(p), "text/csv", "bom.csv")], scratch_root=str(tmp_path))
    try:
        units = [u for bt in plan.batches for u in bt.units]
        assert len(units) > 1 and all(u.parts == len(units) for u in units)
        assert all(open(u.path).readline().startswith("mark,profile") for u in units)
    finally:
        plan.cleanup()


def test_stitch_continuations():
    assert _stitch("| a | b |", "| c | d |") == "| a | b |\n| c | d |"
    assert _stitch("| a | b", " | c |") == "| a | b | c |"
    assert _stitch("Total pie", "ces: 4") == "Total pieces: 4"
    assert _stitch("line\n", "next") == "line\nnext"


def test_finalize_substitutes_tables_aliases_and_renumbers():
    md = ("## SECTION 9\n[[TABLE:T1 | Member Register | columns: # | 3 merged rows]]\n\n"
          "## SECTION 12 — READY-TO-SEND RFI PACKAGE\nRFI-B2-001\nRFI-B1-001\nRFI-B3-004 duplicate\n"
          "Referenced sheets: S-1\n<<<RFI_ALIASES>>>\n{\"RFI-B3-004\": \"RFI-B1-001\"}\n<<<END_RFI_ALIASES>>>")
    out = pipeline._finalize(md, {"T1": "| # | Mark |\n| --- | --- |\n| 1 | B1 |"})
    assert "| 1 | B1 |" in out and "[[TABLE" not in out
    assert "RFI-B" not in out and "RFI_ALIASES" not in out and "Referenced sheets" not in out
    assert out.index("RFI-001") < out.index("RFI-002")
    assert out.count("RFI-002") == 2      # merged duplicate points at the surviving RFI


def test_bulk_tables_merge_rows_and_drop_false_missing():
    head = "| # | Sheet | Status |\n| --- | --- | --- |\n"
    p1 = "SECTION 1\nTable: Drawing Register\n" + head + "".join(f"| {i} | S-{i} | Readable |\n" for i in range(1, 30)) \
        + "| 30 | S-40 | Referenced but Missing |\n"
    p2 = "SECTION 1\nTable: Drawing Register\n" + head + "".join(f"| {i} | S-{i + 30} | Readable |\n" for i in range(1, 30))
    tokenized, merged = pipeline._tokenize_bulk_tables([p1, p2])
    assert all("[[TABLE:T1" in t for t in tokenized)
    table = merged["T1"]
    assert "Missing" not in table               # S-40 exists in the other batch
    data_rows = [ln for ln in table.splitlines()[2:] if ln.startswith("| ")]
    assert len(data_rows) == 58 and data_rows[-1].startswith("| 58 | S-59 |")


def test_rebatch_ids_leaves_grid_references():
    assert pipeline._rebatch_ids("RFI-001, RFI-12, grid C-1, C-B1-03", 4) == "RFI-B4-001, RFI-B4-012, grid C-1, C-B4-03"


ROWS = """<<<META>>>
{"jurisdiction":"USA","sheets":[{"sheet":"S-%(n)d01","title":"Framing"}]}
<<<END_META>>>
<<<ROWS>>>
no|type|role|mark|profile|qty|raw_length|length_mm|unit_wt_kgm|weight_kg|grade|finish|sheet|detail|method|flag
1|W-SHAPE|PRIMARY|B%(n)d|W12x26|2|20'-0"|6096|38.7|472|A992||S-%(n)d01||SCHEDULE|
<<<END_ROWS>>>"""


def test_run_mode_mto_consolidates_all_batches(tmp_path, monkeypatch):
    a = _pdf(tmp_path / "a.pdf", 60)

    async def fake_run_batch(*, system, header_for, units, label, effort=None):
        n = int(re.search(r"batch (\d+) of", header_for(units)).group(1))
        return [BatchOutput(ROWS % {"n": n}, units, "claude-opus-5-5", Usage())]

    monkeypatch.setattr(pipeline, "run_batch", fake_run_batch)
    res = asyncio.run(pipeline.run_mode(mode_id="MTO", system_prompt="", user_text="",
                                        files=[(a, "application/pdf", "Binder.pdf")]))
    md = res.markdown
    assert md.count("## OUTPUT 2") == 1                    # one report, not one per batch
    assert all(f"| B{n} |" in md for n in (1, 2, 3))       # every batch's members present
    assert res.meta["takeoff_summary"]["pieces"] == 6


def test_run_mode_narrative_single_report(tmp_path, monkeypatch):
    a = _pdf(tmp_path / "a.pdf", 60)

    async def fake_run_batch(*, system, header_for, units, label, effort=None):
        assert "BINDER BATCH MODE" in system
        n = int(re.search(r"batch (\d+) of", header_for(units)).group(1))
        return [BatchOutput(f"## SECTION 12 — RFI PACKAGE\nRFI-001 batch {n}", units, "claude-opus-5-5", Usage())]

    async def fake_generate(*, system, content, label, effort=None, client=None):
        assert "CONSOLIDATION MODE" in system
        ids = re.findall(r"RFI-B\d+-\d+", content[0]["text"])
        return GenResult("## SECTION 12 — RFI PACKAGE\n" + "\n".join(dict.fromkeys(ids)), "claude-opus-5-5", Usage())

    monkeypatch.setattr(pipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(pipeline, "generate", fake_generate)
    res = asyncio.run(pipeline.run_mode(mode_id="MASTER_INTAKE", system_prompt="MODE", user_text="",
                                        files=[(a, "application/pdf", "Binder.pdf")]))
    assert res.markdown.count("SECTION 12") == 1
    assert "RFI-001" in res.markdown and "RFI-003" in res.markdown and "RFI-B" not in res.markdown


def test_gather_limited_cancels_siblings_on_failure():
    from claude_engine import gather_limited

    started, finished = [], []

    async def ok(i):
        started.append(i)
        await asyncio.sleep(0.2)
        finished.append(i)
        return i

    async def boom():
        await asyncio.sleep(0.01)
        raise RuntimeError("batch failed")

    async def main():
        with pytest.raises(RuntimeError):
            await gather_limited([boom(), ok(1), ok(2), ok(3)], 2)
        await asyncio.sleep(0.3)

    asyncio.run(main())
    assert finished == []                 # in-flight sibling cancelled, queued ones never ran
    assert started in ([1], [1, 2])


def test_gather_limited_preserves_order():
    from claude_engine import gather_limited

    async def val(i, d):
        await asyncio.sleep(d)
        return i

    assert asyncio.run(gather_limited([val(1, 0.05), val(2, 0.0), val(3, 0.02)], 2)) == [1, 2, 3]
