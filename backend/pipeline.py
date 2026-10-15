"""Analysis pipeline — one binder in, ONE report out.

    files ──► ingestion pre-read (ingest.py: sheet register, text layer + glyph decode,
          │     anchored callout counts, Division 05 metals sweep, scales, vision tiles)
          ──► binder plan (page batches, manifest; each page's tiles in its batch)
          ├─► MTO ...................... structured take-off per batch (with the page digests)
          │                              ─► Python consolidation ─► Master MTO report
          ├─► Estimation Pro ........... (same take-off, cached per drawing set) ─► 2C hours engine
          │                              in code ─► Estimation report
          ├─► Fabricator Estimation .... (same take-off) ─► cost engine ─► estimate
          └─► every other mode ......... one call when the binder fits one batch; otherwise
                                         map (each batch read in parallel, batch-aware framing)
                                         ─► consolidation call (one deliverable, recomputed totals)
                                         ─► large registers merged losslessly in Python
                                         ─► RFI / issue ids renumbered across the whole document

The client never sees batch artefacts: no "batch 3" sections, no repeated headings, no
contradictory per-batch totals.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from binder import BinderPlan, Unit, describe_units, plan_binder
from claude_engine import (
    EngineError,
    Usage,
    effort_for,
    engine_label,
    gather_limited,
    generate,
    reset_wait_hook,
    run_batch,
    set_wait_hook,
)
from config import settings
from estimation import detailing_estimate, fab_estimate
from estimation.takeoff import (
    consolidate,
    parse_meta,
    parse_rows,
    render_mto_report,
    takeoff_batch_header,
    takeoff_system_prompt,
)
from report_merge import normalize_section_headings, parse_blocks, _table_to_md, _sig

logger = logging.getLogger(__name__)

# The deterministic pre-read (register, glyphs, callouts, metals sweep, tiles) runs before
# every take-off. INGEST_ENABLED=false skips it (the model then reads the PDFs alone).
INGEST_ENABLED = os.environ.get("INGEST_ENABLED", "true").lower() in ("1", "true", "yes", "on")

TAKEOFF_MODES = {"MTO"}
FAB_ESTIMATE_MODES = {"FABRICATOR_ESTIMATION_PRO"}
DET_ESTIMATE_MODES = {"ESTIMATION_PRO"}

ProgressFn = Callable[[str, int, int], Awaitable[None]]
CancelFn = Callable[[], Awaitable[bool]]

BULK_MIN_ROWS = 40                # registers at least this long are merged in Python
REDUCE_MAX_CHARS = 1_600_000      # ~450K tokens of partial reports per consolidation call


class Cancelled(Exception):
    """The analysis was deleted while it was running."""


@dataclass
class PipelineResult:
    markdown: str
    engine: str
    meta: dict = field(default_factory=dict)
    takeoff: dict | None = None        # the take-off behind MTO / estimate reports (quality gate input)
    estimate: dict | None = None       # the Estimation Pro dict (quality gate input)
    model_id: str = ""                 # the model that actually served the calls


async def _noop_progress(_label: str, _done: int, _total: int) -> None:
    return None


async def _never_cancel() -> bool:
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Shared batch runner
# ─────────────────────────────────────────────────────────────────────────────

def _describe(units: list[Unit]) -> str:
    return describe_units(units)


async def _run_all_batches(
    plan: BinderPlan, *, system: str, header: Callable[[list[Unit], int, int], str], label: str,
    progress: ProgressFn, cancelled: CancelFn, stage: str, effort: str | None = None,
) -> tuple[list, Usage]:
    """Run every batch (parallel, bounded), retrying a failed batch once. Returns the
    flat list of BatchOutputs in binder order plus the accumulated usage."""
    total = len(plan.batches)
    done = 0
    lock = asyncio.Lock()
    await progress(stage, 0, total)

    async def one(batch) -> list:
        nonlocal done
        if await cancelled():
            raise Cancelled()
        for attempt in (1, 2):
            try:
                outs = await run_batch(
                    system=system,
                    header_for=lambda units, b=batch: header(units, b.index, total),
                    units=batch.units,
                    label=f"{label}-b{batch.index}",
                    effort=effort,
                )
                break
            except EngineError as exc:
                if attempt == 2 or "usage limit" in str(exc):
                    if "usage limit" in str(exc):
                        raise
                    raise EngineError(f"Batch {batch.index} of {total} ({batch.describe()}) failed: {exc}") from exc
                logger.warning("batch_retry label=%s batch=%d error=%s", label, batch.index, exc)
        async with lock:
            done += 1
            await progress(stage, done, total)
        return outs

    results = await gather_limited([one(b) for b in plan.batches], settings.batch_concurrency)
    flat = [o for group in results for o in group]
    usage = Usage()
    for o in flat:
        usage.merge(o.usage)
    return flat, usage


def _primary_model(outputs) -> str:
    models = [o.model for o in outputs if getattr(o, "model", None)]
    return max(set(models), key=models.count) if models else ""


def _primary_engine(outputs) -> str:
    return engine_label(_primary_model(outputs))


def _batch_digest(ingest, units: list[Unit]) -> str:
    """Pre-read digest of exactly the pages attached to this call."""
    if ingest is None:
        return ""
    try:
        from ingest import batch_digest

        entries = []
        for u in units:
            if u.is_tile:
                continue
            entries.extend(ingest.entries_for(u.doc_index, u.page_start, u.page_end))
        return batch_digest(entries) if entries else ""
    except Exception as exc:  # noqa: BLE001 — the digest is an aid, never a blocker
        logger.warning("batch_digest_failed error=%s", exc)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Take-off (MTO / fabricator estimation / AI estimate page)
# ─────────────────────────────────────────────────────────────────────────────

async def run_takeoff(
    plan: BinderPlan, *, user_text: str = "", jurisdiction_hint: str = "USA",
    progress: ProgressFn = _noop_progress, cancelled: CancelFn = _never_cancel, label: str = "takeoff",
    ingest=None,
) -> tuple[dict, str, Usage]:
    if not plan.batches:
        raise EngineError("No readable drawings were uploaded.")
    system = takeoff_system_prompt(jurisdiction_hint)
    manifest = plan.manifest_text()

    def header(units, idx, total):
        return takeoff_batch_header(manifest, _describe(units), idx, total, user_text,
                                    digest=_batch_digest(ingest, units))

    outputs, usage = await _run_all_batches(
        plan, system=system, header=header, label=label, progress=progress, cancelled=cancelled,
        stage="Taking off members", effort=effort_for("takeoff"),
    )
    batches = []
    for i, o in enumerate(outputs, 1):
        meta = parse_meta(o.text)
        rows = parse_rows(o.text)
        if not rows and "<<<ROWS>>>" not in o.text:
            logger.warning("takeoff_batch_unparsed label=%s batch=%d chars=%d", label, i, len(o.text))
        batches.append({"batch": i, "meta": meta, "rows": rows})
    await progress("Computing tonnage", 1, 1)
    takeoff = consolidate(batches, jurisdiction_hint=jurisdiction_hint, documents=plan.summary()["documents"],
                          ingest=ingest)
    if not takeoff["rows"]:
        raise EngineError("No steel members could be taken off from the uploaded drawings.")
    takeoff["engine"] = _primary_engine(outputs)
    takeoff["model_id"] = _primary_model(outputs)
    takeoff["binder"] = plan.summary()
    if ingest is not None:
        takeoff["ingest_summary"] = dict(getattr(ingest, "summary", {}) or {})
    return takeoff, takeoff["engine"], usage


# ─────────────────────────────────────────────────────────────────────────────
# Instruction overrides (rates, factors, quote) from the user's free text
# ─────────────────────────────────────────────────────────────────────────────

_OVERRIDES_SYSTEM = (
    "Extract explicit estimating settings from a fabricator's instructions. Return ONLY a JSON object with any of: "
    "rate_low, rate_mid, rate_high (numbers, per ton), material_type (Carbon Steel|HSS|Galvanised|Stainless|Weathering|"
    "Aluminium), surface_treatment (Shop primer only|SSPC-SP6 + 2-coat|SSPC-SP10 + 3-coat|Hot-dip galvanised|"
    "Intumescent fire-rated), assembly_complexity (Simple bolted|Mixed bolt/weld|Heavy welded|Architectural AESS|"
    "Custom curved), country (USA|Canada|Australia|UK|UAE|India|Europe|Saudi Arabia|Singapore), tax_pct (number), "
    "quote_requested (true only if a client quote/proposal is asked for). Include a key ONLY when the text states it. "
    "Return {} when nothing is stated."
)


async def extract_overrides(user_text: str) -> dict:
    text = (user_text or "").strip()
    if not text:
        return {}
    try:
        res = await generate(system=_OVERRIDES_SYSTEM, content=[{"type": "text", "text": text}],
                             label="overrides", effort="medium")
        m = re.search(r"\{.*\}", res.text, re.S)
        data = json.loads(m.group(0)) if m else {}
        return {k: v for k, v in data.items() if v not in (None, "", [], {})} if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 — overrides are optional
        logger.warning("override_extract_failed error=%s", exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Narrative modes: map → consolidate
# ─────────────────────────────────────────────────────────────────────────────

MAP_FRAMING = """
================================================================
BINDER BATCH MODE (this call covers one slice of a larger binder)
================================================================
The user turn names the pages attached to this call; other pages of the same binder are
reviewed in parallel calls and a consolidation step merges every batch into the single
final deliverable. Therefore:
- Report everything the MODE asks for that appears on THESE pages, in the MODE's section
  structure and table formats, with full detail — every row, every finding.
- Do not draw project-level conclusions from this slice: where the MODE asks for totals,
  scores, verdicts (GO / HOLD), completeness judgements or costs, give only what these
  pages support and label it "this batch". The consolidation recomputes them.
- Never report a sheet, discipline or scope item as missing just because it is not in this
  batch — other batches hold other pages. Do not write "Referenced but Missing" rows;
  instead end with one line "Referenced sheets: <every sheet number called out on these pages>".
- Number RFIs RFI-B{n}-001, RFI-B{n}-002 … and any issue / conflict ids with the same
  B{n} infix (C-B{n}-01, ISS-B{n}-01), where {n} is this batch number, so ids never collide.
"""

REDUCE_FRAMING = """
================================================================
CONSOLIDATION MODE (final deliverable for the whole binder)
================================================================
The user turn contains extraction reports that together cover EVERY page of ONE binder
(the BINDER MANIFEST lists all documents). Write the single final deliverable exactly as
the MODE specifies — one document, one set of sections in the MODE's order, as if you had
read the entire binder in one sitting:
- Merge, never append: one section per MODE section; rows from all reports combined into
  one table; exact duplicates removed; rows describing the same item (same sheet / mark /
  grid / connection) merged, keeping the most complete information.
- Recompute every count, total, score, grade and verdict from the merged content.
- A sheet, discipline or scope item is missing only if NO report contains it. Use the
  "Referenced sheets" lines across all reports and the manifest to decide.
- Keep every distinct finding, conflict and RFI; merge duplicates.
- Never mention batches, extraction reports, partial reviews or this consolidation.
- Ids: keep the ids exactly as written (RFI-B2-004, C-B1-03 …) — the platform renumbers
  them sequentially afterwards. When you merge duplicate RFIs keep one id, and end your
  output with this block (omit it when nothing was merged):
  <<<RFI_ALIASES>>>
  {"RFI-B3-002": "RFI-B1-005"}
  <<<END_RFI_ALIASES>>>
- Platform-merged tables: some large tables are already merged and appear as one token
  line such as [[TABLE:T1 | ...]]. Write each token line exactly once, alone on its own
  line, where that table belongs in the MODE structure. Do not rewrite those tables and do
  not reproduce their rows; you may cite the row counts given in the token.
"""


def _single_header(manifest: str, desc: str, user_text: str, batch_no: int = 1, batch_total: int = 1) -> str:
    instructions = (user_text or "").strip() or "None."
    if batch_total > 1:
        scope = f"THIS CALL: batch {batch_no} of {batch_total} — {desc}."
    else:
        scope = f"ATTACHED: the complete binder — {desc}."
    return (f"{manifest}\n\n{scope}\n\nUSER INSTRUCTIONS (the MODE rules take precedence on conflict):\n"
            f"{instructions}\n\nProduce the deliverable now.")


_ID_RE = re.compile(r"\b([A-Z]{1,6})-B(\d+)-(\d+)\b")


def _tokenize_bulk_tables(partials: list[str]) -> tuple[list[str], dict[str, str]]:
    """Replace large registers (same header across reports) with tokens; return the
    tokenized partials and token → merged-table markdown."""
    parsed = [parse_blocks(p) for p in partials]
    by_sig: dict[tuple, dict] = {}
    order: list[tuple] = []
    for doc in parsed:
        last_caption = ""
        for blk in doc:
            if blk[0] == "heading":
                last_caption = blk[2]
            elif blk[0] == "text":
                m = re.search(r"(?im)^\s*\**\s*Table:\s*(.+?)\s*\**\s*$", blk[1])
                if m:
                    last_caption = m.group(1)
            elif blk[0] == "table":
                sig = _sig(blk[1])
                if sig not in by_sig:
                    by_sig[sig] = {"header": blk[1], "align": blk[2], "rows": [], "caption": last_caption}
                    order.append(sig)
                by_sig[sig]["rows"].extend(blk[3])

    tokens: dict[tuple, str] = {}
    merged: dict[str, str] = {}
    n = 0
    for sig in order:
        info = by_sig[sig]
        rows = _merge_rows(info["header"], info["rows"])
        if len(rows) < BULK_MIN_ROWS:
            continue
        n += 1
        tid = f"T{n}"
        cols = " / ".join(info["header"][:10])
        token = f"[[TABLE:{tid} | {info['caption'] or 'Register'} | columns: {cols} | {len(rows)} merged rows]]"
        tokens[sig] = token
        merged[tid] = _table_to_md(info["header"], info["align"], rows)

    if not tokens:
        return partials, {}
    out = []
    for doc in parsed:
        pieces = []
        for blk in doc:
            if blk[0] == "heading":
                pieces.append("#" * blk[1] + " " + blk[2])
            elif blk[0] == "text":
                pieces.append(blk[1])
            else:
                sig = _sig(blk[1])
                pieces.append(tokens[sig] if sig in tokens else _table_to_md(blk[1], blk[2], blk[3]))
        out.append("\n\n".join(pieces))
    return out, merged


def _merge_rows(header: list[str], rows: list[list[str]]) -> list[list[str]]:
    """Union of rows across reports: exact duplicates dropped, 'missing' rows dropped when
    another report has the same item, running number column renumbered."""
    numbered = bool(header) and re.sub(r"[^a-z#]", "", header[0].lower()) in ("#", "no", "item", "sno", "sr")
    key_i = 1 if numbered and len(header) > 1 else 0
    seen: set[str] = set()
    uniq: list[list[str]] = []
    for r in rows:
        body = r[1:] if numbered else r
        k = "|".join(re.sub(r"\s+", " ", c.strip().lower()) for c in body)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(list(r))
    present = {}
    for r in uniq:
        key = r[key_i].strip().lower() if key_i < len(r) else ""
        if key and "missing" not in " ".join(r).lower():
            present[key] = True
    out = [r for r in uniq if not ("missing" in " ".join(r).lower()
                                   and key_i < len(r) and present.get(r[key_i].strip().lower()))]
    if numbered:
        for i, r in enumerate(out, 1):
            if r:
                r[0] = str(i)
    return out


def _apply_aliases(md: str) -> str:
    m = re.search(r"<<<RFI_ALIASES>>>(.*?)<<<END_RFI_ALIASES>>>", md, re.S)
    if not m:
        return md
    md = md[: m.start()].rstrip() + md[m.end():]
    try:
        aliases = json.loads(m.group(1).strip() or "{}")
    except Exception:  # noqa: BLE001
        return md
    for src, dst in (aliases or {}).items():
        if isinstance(src, str) and isinstance(dst, str) and _ID_RE.fullmatch(src.strip()):
            md = re.sub(rf"\b{re.escape(src.strip())}\b", dst.strip(), md)
    return md


def _renumber_ids(md: str) -> str:
    """RFI-B2-004 → RFI-007 etc., numbered by order within the section that defines them
    (the last section whose heading names the family), then by first appearance."""
    families: dict[str, list[str]] = {}
    for fam, b, n in _ID_RE.findall(md):
        ident = f"{fam}-B{b}-{n}"
        families.setdefault(fam, [])
        if ident not in families[fam]:
            families[fam].append(ident)
    if not families:
        return md
    lines = md.splitlines()
    mapping: dict[str, str] = {}
    for fam, ids in families.items():
        heading_idx = [i for i, ln in enumerate(lines)
                       if fam == "RFI" and ln.lstrip().startswith("#") and "RFI" in ln.upper()]
        ordered: list[str] = []
        if heading_idx:
            start = heading_idx[-1]
            end = next((i for i in range(start + 1, len(lines)) if re.match(r"^#{1,2}\s", lines[i])), len(lines))
            for ln in lines[start:end]:
                for f2, b, n in _ID_RE.findall(ln):
                    ident = f"{f2}-B{b}-{n}"
                    if f2 == fam and ident not in ordered:
                        ordered.append(ident)
        ordered += [i for i in ids if i not in ordered]
        width = 3 if fam == "RFI" else 2
        for k, ident in enumerate(ordered, 1):
            mapping[ident] = f"{fam}-{k:0{width}d}"
    return _ID_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), md)


def _substitute_tables(md: str, merged: dict[str, str]) -> str:
    used: set[str] = set()

    def repl(m: re.Match) -> str:
        tid = m.group(1)
        if tid in merged and tid not in used:
            used.add(tid)
            return merged[tid]
        return ""

    md = re.sub(r"(?m)^[^\n]*\[\[TABLE:(T\d+)[^\]]*\]\][^\n]*$", repl, md)
    leftovers = [tid for tid in merged if tid not in used]
    if leftovers:
        md += "\n\n## CONSOLIDATED REGISTERS\n\n" + "\n\n".join(merged[t] for t in leftovers)
    return md


def _finalize(md: str, merged: dict[str, str]) -> str:
    md = _substitute_tables(md, merged)
    md = _apply_aliases(md)
    md = _renumber_ids(md)
    md = re.sub(r"(?im)^\s*referenced sheets:.*$", "", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return normalize_section_headings(md)


async def _consolidate(system: str, manifest: str, partials: list[str], user_text: str, label: str) -> tuple[str, Usage, str]:
    tokenized, merged = _tokenize_bulk_tables(partials)
    usage = Usage()
    # Very large binders: consolidate in groups first, then consolidate the groups.
    groups: list[list[str]] = [[]]
    size = 0
    for p in tokenized:
        if groups[-1] and size + len(p) > REDUCE_MAX_CHARS:
            groups.append([])
            size = 0
        groups[-1].append(p)
        size += len(p)

    async def reduce_once(parts: list[str], tag: str) -> tuple[str, str]:
        body = "\n\n".join(f"===== REPORT {i} =====\n{p}" for i, p in enumerate(parts, 1))
        instructions = (user_text or "").strip() or "None."
        text = (f"{manifest}\n\nUSER INSTRUCTIONS: {instructions}\n\n"
                f"The reports below together cover the complete binder.\n\n{body}\n\n"
                "Write the single consolidated deliverable now.")
        res = await generate(system=system + REDUCE_FRAMING, content=[{"type": "text", "text": text}], label=f"{label}-{tag}")
        usage.merge(res.usage)
        return res.text, res.model

    model = ""
    if len(groups) > 1:
        intermediates = await gather_limited(
            [reduce_once(g, f"reduce{i}") for i, g in enumerate(groups, 1)], settings.batch_concurrency)
        final, model = await reduce_once([t for t, _ in intermediates], "reduce-final")
    else:
        final, model = await reduce_once(groups[0], "reduce")
    return _finalize(final, merged), usage, model


async def run_narrative(
    plan: BinderPlan, *, system: str, user_text: str = "", label: str = "analysis",
    progress: ProgressFn = _noop_progress, cancelled: CancelFn = _never_cancel,
) -> tuple[str, str, Usage]:
    manifest = plan.manifest_text()
    if not plan.batches:
        # No attachments: text-only run (e.g. chat assistant with typed context).
        res = await generate(system=system, content=[{"type": "text", "text": _single_header(manifest, "no files", user_text)}],
                             label=label)
        return normalize_section_headings(res.text), engine_label(res.model), res.usage

    if plan.is_single_call:
        await progress("Analysing drawings", 0, 1)
        outs = await run_batch(
            system=system, units=plan.batches[0].units, label=label,
            header_for=lambda units: _single_header(manifest, _describe(units), user_text),
        )
        usage = Usage()
        for o in outs:
            usage.merge(o.usage)
        await progress("Analysing drawings", 1, 1)
        if len(outs) == 1:
            return normalize_section_headings(outs[0].text), engine_label(outs[0].model), usage
        # An attachment had to be isolated — consolidate the pieces into one report.
        await progress("Consolidating report", 0, 1)
        text, u2, model = await _consolidate(system, manifest, [o.text for o in outs], user_text, label)
        usage.merge(u2)
        return text, engine_label(model), usage

    map_system = system + MAP_FRAMING

    def header(units, idx, total):
        return _single_header(manifest, _describe(units), user_text, idx, total)

    outs, usage = await _run_all_batches(
        plan, system=map_system, header=header, label=label, progress=progress, cancelled=cancelled,
        stage="Reading binder",
    )
    if await cancelled():
        raise Cancelled()
    await progress("Consolidating report", 0, 1)
    partials = [_rebatch_ids(o.text, i) for i, o in enumerate(outs, 1)]
    text, u2, model = await _consolidate(system, manifest, partials, user_text, label)
    usage.merge(u2)
    await progress("Consolidating report", 1, 1)
    return text, engine_label(model), usage


def _rebatch_ids(text: str, idx: int) -> str:
    """Bisected sub-batches share their parent's batch number; give every output its own
    id space, and bring un-prefixed RFI ids into the batch namespace."""
    text = _ID_RE.sub(lambda m: f"{m.group(1)}-B{idx}-{m.group(3)}", text)
    return re.sub(r"\bRFI-(\d{1,4})\b", lambda m: f"RFI-B{idx}-{int(m.group(1)):03d}", text)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def _pre_read(files, work_dir: str, progress: ProgressFn):
    """Run the deterministic ingestion pre-read in a worker thread (never fails the job)."""
    if not INGEST_ENABLED or not files:
        return None
    try:
        from ingest import run_ingest
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest_unavailable error=%s", exc)
        return None
    await progress("Pre-reading drawings (register, callouts, metals sweep, tiles)", 0, 1)
    try:
        ing = await asyncio.to_thread(run_ingest, files, work_dir)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest_failed error=%s", exc)
        return None
    await progress("Pre-reading drawings (register, callouts, metals sweep, tiles)", 1, 1)
    return ing


async def run_mode(
    *, mode_id: str, system_prompt: str, user_text: str, files: list[tuple[str, str, str]],
    skipped: list[str] | None = None, progress: ProgressFn = _noop_progress,
    cancelled: CancelFn = _never_cancel, label: str = "analysis", jurisdiction_hint: str = "USA",
    takeoff_cache=None, work_dir: str | None = None,
) -> PipelineResult:
    """Run one analysis mode over a binder and return the single final report.

    takeoff_cache: optional object with async get() -> dict|None and async put(dict),
    used so MTO, Estimation Pro, Fabricator Estimation and the estimate page share one take-off.
    work_dir: where the pre-read writes its artefacts (sheet_register.json, callouts.json …)
    and tiles; a temporary directory (removed afterwards) when not given.
    """
    async def waiting(_label: str) -> None:
        await progress("Waiting for model capacity", 0, 1)

    token = set_wait_hook(waiting)
    own_work = work_dir is None
    work = work_dir or tempfile.mkdtemp(prefix="ingest_")
    try:
        if mode_id in TAKEOFF_MODES | FAB_ESTIMATE_MODES | DET_ESTIMATE_MODES:
            return await _run_takeoff_mode(
                mode_id=mode_id, user_text=user_text, files=files, skipped=skipped, progress=progress,
                cancelled=cancelled, label=label, jurisdiction_hint=jurisdiction_hint,
                takeoff_cache=takeoff_cache, work_dir=work)
        plan = plan_binder(files, skipped=skipped)
        try:
            text, engine, usage = await run_narrative(
                plan, system=system_prompt, user_text=user_text, label=label, progress=progress, cancelled=cancelled)
            meta = {"binder": plan.summary(), "kind": "narrative", "usage": usage.as_dict()}
            return PipelineResult(text, engine, meta)
        finally:
            plan.cleanup()
    finally:
        reset_wait_hook(token)
        if own_work:
            shutil.rmtree(work, ignore_errors=True)


async def _run_takeoff_mode(
    *, mode_id: str, user_text: str, files, skipped, progress: ProgressFn, cancelled: CancelFn, label: str,
    jurisdiction_hint: str, takeoff_cache, work_dir: str,
) -> PipelineResult:
    takeoff = None
    if mode_id not in TAKEOFF_MODES and takeoff_cache is not None:
        takeoff = await takeoff_cache.get()
    usage = Usage()
    if takeoff is None:
        ing = await _pre_read(files, work_dir, progress)
        if await cancelled():
            raise Cancelled()
        plan = plan_binder(files, skipped=skipped, tiles=getattr(ing, "tiles", None))
        try:
            takeoff, _engine, usage = await run_takeoff(
                plan, user_text=user_text, jurisdiction_hint=jurisdiction_hint,
                progress=progress, cancelled=cancelled, label=label, ingest=ing)
        finally:
            plan.cleanup()
        if takeoff_cache is not None:
            await takeoff_cache.put(takeoff)
    engine = takeoff.get("engine") or engine_label("")
    model_id = takeoff.get("model_id") or ""
    meta = {"binder": takeoff.get("binder"), "kind": "takeoff", "takeoff_summary": _takeoff_summary(takeoff),
            "usage": usage.as_dict(), "ingest_summary": takeoff.get("ingest_summary")}

    if mode_id in TAKEOFF_MODES:
        return PipelineResult(render_mto_report(takeoff), engine, meta, takeoff=takeoff, model_id=model_id)

    if mode_id in DET_ESTIMATE_MODES:
        await progress("Computing detailing hours", 0, 1)
        est = detailing_estimate.from_takeoff(takeoff, region_hint=jurisdiction_hint)
        meta.update(kind="detailing_estimate", estimate={
            k: est.get(k) for k in ("lock_hrs_low", "lock_hrs_high", "lock_cost_low", "lock_cost_high", "currency",
                                    "completeness", "buffer_pct")})
        await progress("Computing detailing hours", 1, 1)
        return PipelineResult(detailing_estimate.render(est, user_text=user_text), engine, meta,
                              takeoff=takeoff, estimate=est, model_id=model_id)

    overrides = dict(takeoff.get("user_overrides") or {})
    overrides.update(await extract_overrides(user_text))
    est = fab_estimate.compute({**takeoff, "user_overrides": overrides})
    meta["estimate"] = {k: est[k] for k in ("tons", "ton_name", "currency", "lock", "rate_basis")}
    return PipelineResult(fab_estimate.render(takeoff, est, user_text=user_text), engine, meta,
                          takeoff=takeoff, model_id=model_id)


def _takeoff_summary(t: dict) -> dict:
    tot = t["totals"]
    usa = t["jurisdiction"] == "USA"
    return {
        "jurisdiction": t["jurisdiction"],
        "net_kg": tot["net_kg"], "gross_kg": tot["gross_kg"], "gross_t": tot["gross_t"],
        "gross_lb": tot.get("gross_lb"), "gross_short_tons": tot["gross_short_tons"],
        "est_tonnage": tot.get("est_tonnage", tot["gross_short_tons"] if usa else tot["gross_t"]),
        "est_tonnage_unit": tot.get("est_tonnage_unit", "tons" if usa else "t"),
        "pieces": tot["pieces"], "line_items": tot["line_items"],
        "rfis": len(t["rfis"]), "sheets": len(t.get("register") or t["sheets"]),
        "coverage": t.get("coverage"), "confidence": t.get("confidence"),
        "project": (t.get("project") or {}).get("name", ""),
    }
