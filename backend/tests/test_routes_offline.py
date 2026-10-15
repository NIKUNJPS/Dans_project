"""End-to-end route tests against an in-memory MongoDB with Claude stubbed.

Exercises the real FastAPI routes: upload a multi-batch binder, run the MTO Engine and
Master Intake, read the single consolidated report, download an export, price the same
take-off on the estimate page (cache hit — no second read of the drawings), and delete
history (one report, a project's history, Quick Analysis history, an estimate).
Requires `mongomock-motor`; skipped when it is not installed.
"""
import os
import re
import tempfile

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="sm_uploads_"))

import pytest

mongomock_motor = pytest.importorskip("mongomock_motor")
pymupdf = pytest.importorskip("pymupdf")

from fastapi.testclient import TestClient  # noqa: E402

import db as db_module  # noqa: E402
import pipeline  # noqa: E402
from claude_engine import BatchOutput, GenResult, Usage  # noqa: E402
from permissions import default_permissions  # noqa: E402
from security import create_access_token  # noqa: E402

ROWS = """<<<META>>>
{"jurisdiction":"USA","sheets":[{"sheet":"S-%(n)d01","title":"Framing %(n)d"}],
 "rfis":[{"id":"RFI-1","priority":"STANDARD","sheet":"S-%(n)d01","question":"Confirm finish for batch %(n)d."}]}
<<<END_META>>>
<<<ROWS>>>
no|type|role|mark|profile|qty|raw_length|length_mm|unit_wt_kgm|weight_kg|grade|finish|sheet|detail|method|flag
1|W-SHAPE|PRIMARY|B%(n)d|W12x26|3|20'-0"|6096|38.7|708|A992|PRIMER|S-%(n)d01||SCHEDULE|
2|JOIST|SECONDARY|J%(n)d|18K4|10|30'-0"|9144|||||S-%(n)d01||PLAN|
<<<END_ROWS>>>"""

CALLS = {"batches": 0}


async def fake_run_batch(*, system, header_for, units, label, effort=None):
    CALLS["batches"] += 1
    m = re.search(r"batch (\d+) of", header_for(units))
    n = int(m.group(1)) if m else 1
    if "senior steel detailing estimator" in system:
        return [BatchOutput(ROWS % {"n": n}, units, "claude-opus-5-5", Usage())]
    return [BatchOutput(f"## SECTION 12 — RFI PACKAGE\nRFI-001 question {n}", units, "claude-opus-5-5", Usage())]


async def fake_generate(*, system, content, label, effort=None, client=None):
    if label == "overrides":
        return GenResult("{}", "claude-opus-5-5", Usage())
    ids = re.findall(r"RFI-B\d+-\d+", content[0]["text"])
    return GenResult("## EXECUTIVE SNAPSHOT\n- ok\n\n## SECTION 12 — RFI PACKAGE\n" + "\n".join(dict.fromkeys(ids)),
                     "claude-opus-5-5", Usage())


@pytest.fixture()
def client(monkeypatch):
    db_module._db = mongomock_motor.AsyncMongoMockClient()["routes_test"]
    monkeypatch.setattr(pipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(pipeline, "generate", fake_generate)
    CALLS["batches"] = 0
    import server

    c = TestClient(server.app)
    database = db_module._db
    import asyncio

    async def seed():
        await database.users.insert_one({"id": "u1", "email": "fab@example.com", "role": "fabricator",
                                         "first_name": "Fab", "last_name": "User", "is_active": True,
                                         "country": "USA"})
        perms = default_permissions("fabricator")
        perms.update(userId="u1", role="fabricator", allowedModes=["*"], analysesPerMonth=-1,
                     allowedExports=["pdf", "markdown", "word", "excel", "csv"])
        await database.feature_permissions.insert_one(perms)

    asyncio.run(seed())
    c.headers.update({"Authorization": f"Bearer {create_access_token('u1', 'fabricator')}"})
    yield c
    db_module._db = None


def _binder(path, pages=60):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=600, height=400).insert_text((40, 40), f"S-{i + 1:03d}")
    doc.save(str(path))
    return path


def _upload(client, path, project_id=None):
    data = {"project_id": project_id} if project_id else {}
    with open(path, "rb") as fh:
        r = client.post("/api/files/upload", files={"file": (os.path.basename(path), fh, "application/pdf")}, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_mto_report_estimate_and_history(client, tmp_path):
    project = client.post("/api/projects", json={"name": "Warehouse", "description": "", "tags": []}).json()
    f = _upload(client, _binder(tmp_path / "binder.pdf"), project["id"])

    r = client.post("/api/analyses", json={"project_id": project["id"], "file_ids": [f["id"]], "mode": "MTO"})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    a = client.get(f"/api/analyses/{aid}").json()
    assert a["status"] == "complete", a.get("error_message")
    md = a["output_markdown"]
    assert md.count("## OUTPUT 2") == 1 and "18K4" not in md
    assert a["takeoff_summary"]["pieces"] == 9 and a["binder"]["batches"] == 3
    batches_after_mto = CALLS["batches"]

    pdf = client.get(f"/api/analyses/{aid}/export/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # Every format downloads. With the export folder wiped (e.g. a redeploy), a download
    # builds only the format asked for, so one format never waits on the others.
    from export_service import EXPORT_DIR
    for ext in ("pdf", "docx", "xlsx", "csv", "md"):
        (EXPORT_DIR / f"{aid}.{ext}").unlink(missing_ok=True)
    docx = client.get(f"/api/analyses/{aid}/export/docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert not (EXPORT_DIR / f"{aid}.pdf").exists() and not (EXPORT_DIR / f"{aid}.xlsx").exists()
    for fmt, magic in (("pdf", b"%PDF"), ("xlsx", b"PK"), ("csv", b"\xef\xbb\xbfsection"), ("markdown", b"# ")):
        r = client.get(f"/api/analyses/{aid}/export/{fmt}")
        assert r.status_code == 200 and r.content.startswith(magic), fmt

    # Fabricator Estimation Pro prices the SAME cached take-off — no second read.
    r = client.post("/api/analyses", json={"project_id": project["id"], "file_ids": [f["id"]],
                                           "mode": "FABRICATOR_ESTIMATION_PRO"})
    est = client.get(f"/api/analyses/{r.json()['id']}").json()
    assert est["status"] == "complete", est.get("error_message")
    assert CALLS["batches"] == batches_after_mto
    assert "CALCULATION MANIFEST" in est["output_markdown"]

    # Estimate page: background job, then poll.
    r = client.post("/api/estimation/ai-calculate", json={"role": "fabricator", "country": "USA",
                                                          "rate_low": 3000, "rate_high": 4000,
                                                          "file_ids": [f["id"]], "project_id": project["id"]})
    assert r.status_code == 200, r.text
    e = client.get(f"/api/estimation/{r.json()['id']}").json()
    assert e["status"] == "complete", e.get("error_message")
    assert e["result"]["visible"]["extracted"]["ton_unit"] == "short ton"
    assert CALLS["batches"] == batches_after_mto
    assert client.get(f"/api/estimation/{e['id']}/pdf").status_code == 200

    # Project history: list then clear (reports + estimates), drawings kept.
    hist = client.get(f"/api/analyses?project_id={project['id']}").json()
    assert hist["total"] == 2
    r = client.post("/api/analyses/bulk-delete", json={"project_id": project["id"]})
    assert r.json()["deleted"] == 2 and r.json()["estimates_deleted"] == 1
    assert client.get(f"/api/analyses?project_id={project['id']}").json()["total"] == 0
    assert client.get(f"/api/files?project_id={project['id']}").json()["items"]
    from export_service import EXPORT_DIR
    assert not (EXPORT_DIR / f"{aid}.pdf").exists()


def test_quick_analysis_master_intake_and_delete(client, tmp_path):
    f = _upload(client, _binder(tmp_path / "quick.pdf"))
    r = client.post("/api/analyses", json={"file_ids": [f["id"]], "mode": "MASTER_INTAKE"})
    aid = r.json()["id"]
    a = client.get(f"/api/analyses/{aid}").json()
    assert a["status"] == "complete", a.get("error_message")
    assert a["output_markdown"].count("SECTION 12") == 1 and "RFI-B" not in a["output_markdown"]
    assert a["progress"]["label"] == "Complete"

    quick = client.get("/api/analyses?quick=true").json()
    assert [x["id"] for x in quick["items"]] == [aid]

    r2 = client.post("/api/analyses", json={"file_ids": [f["id"]], "mode": "SUMMARIZER"}).json()
    assert client.delete(f"/api/analyses/{r2['id']}").status_code == 200
    assert client.post("/api/analyses/bulk-delete", json={"scope": "quick"}).json()["deleted"] == 1
    assert client.get("/api/analyses?quick=true").json()["total"] == 0


def test_report_names_rename_and_open_downloads(client, tmp_path):
    from urllib.parse import unquote

    import db as dbm
    import asyncio

    # Downloads and printing need no permission: even an account with every export
    # switch turned off can download every format.
    async def lock_down():
        await dbm._db.feature_permissions.update_one(
            {"userId": "u1"}, {"$set": {"allowedExports": [], "canDownloadReports": False}})
    asyncio.run(lock_down())

    f = _upload(client, _binder(tmp_path / "Riverside Bid Set.pdf", pages=10))

    # Unnamed Quick Analysis -> named after its first drawing.
    a = client.post("/api/analyses", json={"file_ids": [f["id"]], "mode": "SUMMARIZER"}).json()
    assert a["title"] == "Riverside Bid Set"

    # Named at creation.
    r = client.post("/api/analyses", json={"file_ids": [f["id"]], "mode": "SUMMARIZER", "title": "  Tower C — Rev 2 "})
    aid = r.json()["id"]
    assert client.get(f"/api/analyses/{aid}").json()["title"] == "Tower C — Rev 2"

    d = client.get(f"/api/analyses/{aid}/export/docx")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    assert "Tower_C_Rev_2_" in unquote(d.headers["content-disposition"])
    for fmt in ("pdf", "xlsx", "csv", "markdown"):
        assert client.get(f"/api/analyses/{aid}/export/{fmt}").status_code == 200, fmt

    # Rename: blank names are refused; a rename rebuilds the exports with the new name.
    assert client.patch(f"/api/analyses/{aid}", json={"title": "   "}).status_code == 422
    r = client.patch(f"/api/analyses/{aid}", json={"title": "North Tower MTO"})
    assert r.status_code == 200 and r.json()["title"] == "North Tower MTO"
    md = client.get(f"/api/analyses/{aid}/export/markdown")
    assert md.text.startswith("# ") and "**Report:** North Tower MTO" in md.text
    assert "North_Tower_MTO_" in unquote(md.headers["content-disposition"])
    quick = client.get("/api/analyses?quick=true").json()["items"]
    assert {x["title"] for x in quick} == {"Riverside Bid Set", "North Tower MTO"}


def test_project_create_rename_delete(client, tmp_path):
    p = client.post("/api/projects", json={"name": "  Warehouse A ", "description": "", "tags": []}).json()
    assert p["name"] == "Warehouse A"
    f = _upload(client, _binder(tmp_path / "set.pdf", pages=5), p["id"])
    a = client.post("/api/analyses", json={"project_id": p["id"], "file_ids": [f["id"]], "mode": "SUMMARIZER"}).json()
    assert a["title"] == ""  # project reports are named after their project unless the user names them

    assert client.put(f"/api/projects/{p['id']}", json={"name": ""}).status_code == 422
    r = client.put(f"/api/projects/{p['id']}", json={"name": "Warehouse B", "description": "Phase 2"})
    assert r.status_code == 200 and r.json()["name"] == "Warehouse B"
    got = client.get(f"/api/analyses/{a['id']}").json()
    assert got["project_name"] == "Warehouse B"
    md = client.get(f"/api/analyses/{a['id']}/export/markdown")
    assert "**Project:** Warehouse B" in md.text

    assert client.delete(f"/api/projects/{p['id']}?hard=true").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    assert client.get(f"/api/analyses/{a['id']}").status_code == 404


def test_old_quick_reports_get_named_and_renames_during_a_run_are_kept(client, tmp_path, monkeypatch):
    import asyncio
    from urllib.parse import unquote

    import db as dbm
    from routes.analyses import backfill_report_titles

    f = _upload(client, _binder(tmp_path / "Harbour Bid Set.pdf", pages=5))

    # A Quick Analysis saved before reports had names.
    async def legacy():
        await dbm._db.analyses.insert_one({
            "id": "legacy1", "project_id": None, "project_name": "Quick Analysis", "file_ids": [f["id"]],
            "requested_by": "u1", "mode": "SUMMARIZER", "mode_label": "Quick Summary", "status": "complete",
            "output_markdown": "## Summary\nOld report.", "blockchain_hash": "x", "exports": []})
        return await backfill_report_titles(dbm._db)
    assert asyncio.run(legacy()) == 1
    assert client.get("/api/analyses/legacy1").json()["title"] == "Harbour Bid Set"
    r = client.get("/api/analyses/legacy1/export/markdown")
    assert "Harbour_Bid_Set_" in unquote(r.headers["content-disposition"]) and "**Report:** Harbour Bid Set" in r.text

    # Renamed while the analysis is still running: the exports carry the new name.
    original = pipeline.run_batch

    async def rename_mid_run(**kw):
        aid = (await dbm._db.analyses.find_one({"status": {"$ne": "complete"}}, {"_id": 0, "id": 1}))["id"]
        await dbm._db.analyses.update_one({"id": aid}, {"$set": {"title": "Renamed Mid Run"}})
        return await original(**kw)
    monkeypatch.setattr(pipeline, "run_batch", rename_mid_run)
    aid = client.post("/api/analyses", json={"file_ids": [f["id"]], "mode": "SUMMARIZER", "title": "Before"}).json()["id"]
    md = client.get(f"/api/analyses/{aid}/export/markdown")
    assert "**Report:** Renamed Mid Run" in md.text


def test_download_names_keep_every_script():
    from export_service import download_name
    assert download_name("टावर परियोजना", "MTO") == "टावर_परियोजना_MTO"
    assert download_name("Résumé", "MTO") == "Résumé_MTO"
    assert download_name('a"b\r\n;c', "") == "a_b_c"
    assert download_name("", "") == "report"
