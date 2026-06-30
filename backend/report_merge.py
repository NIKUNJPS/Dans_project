"""Deterministic, lossless merge of per-batch analysis outputs into ONE report.

WHY THIS EXISTS (and why it is NOT an LLM merge)
─────────────────────────────────────────────────
When a drawing set is split into batches for upload, each batch produces its own
markdown report — for an MTO that can be hundreds or thousands of table rows.
Asking an LLM to "merge" the batches forces it to re-emit every row, which
truncates at the output-token limit and silently drops rows. That is the single
biggest cause of an incomplete take-off.

This module merges in pure Python so that:

  • EVERY data row from EVERY batch is preserved — nothing is summarised, sampled
    or collapsed (the client never sees a 'batch' artefact either).
  • Tables that describe the same thing across batches (identical column headers)
    are fused into ONE table by concatenating their data rows.
  • Project totals are RECOMPUTED by summation from the merged detail table, never
    re-estimated, so the headline tonnage/quantities are arithmetically correct.

The result is a single, coherent markdown report identical in structure to what a
single-batch run would have produced — only complete.
"""
from __future__ import annotations

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

# A markdown table separator row, e.g.  | :-- | --: | :-: |
_SEP_RE = re.compile(r"^\s*\|?[\s\-:|]+\|?\s*$")
# "Total pieces extracted: 1234"  /  "Total rows: 1234" — refreshed after merge.
_TOTAL_PIECES_RE = re.compile(
    r"(total\s+(?:pieces\s+extracted|rows|members|items)\s*[:=]\s*)([0-9,]+)",
    re.IGNORECASE,
)

# Header tokens that identify the per-category SUMMARY table (Output 3 of the MTO
# mode). These are recomputed from the merged detail table rather than concatenated
# (concatenating per-batch subtotals would double-count).
_SUMMARY_HEADER_HINTS = ("category", "member count")

# Header tokens that identify the DETAIL take-off table (Output 2 of the MTO mode).
_DETAIL_HEADER_HINTS = ("mark", "profile", "est wt")

# Column-name fuzzy matches used when recomputing the summary.
_TYPE_COL_HINTS = ("type", "category")
_WT_KG_HINTS = ("est wt (kg)", "est total wt (kg)", "weight (kg)", "wt (kg)")
_WT_LB_HINTS = ("est wt (lbs)", "est total wt (lbs)", "weight (lbs)", "wt (lbs)")
_LEN_M_HINTS = ("length (m)", "total length (m)", "len (m)")


# ─────────────────────────────────────────────────────────────────────────────
# Block model
# ─────────────────────────────────────────────────────────────────────────────
# A parsed document is a flat, ordered list of blocks. Each block is one of:
#   ("heading", level:int, text:str)
#   ("table",   header:list[str], align:str, rows:list[list[str]])
#   ("text",    raw:str)               # paragraphs, lists, hr, blank-separated prose
# ─────────────────────────────────────────────────────────────────────────────


def _norm_cell(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _split_row(line: str) -> List[str]:
    return [_norm_cell(c) for c in line.strip().strip("|").split("|")]


def _sig(header: List[str]) -> Tuple[str, ...]:
    """Normalised header signature used to decide whether two tables are 'the same'."""
    return tuple(re.sub(r"[^a-z0-9]", "", c.lower()) for c in header)


def _header_has(header: List[str], hints: tuple[str, ...]) -> bool:
    joined = " | ".join(c.lower() for c in header)
    return any(h in joined for h in hints)


def _find_col(header: List[str], hints: tuple[str, ...]) -> int:
    """Return the index of the first column whose name matches any hint, else -1."""
    low = [c.lower().strip() for c in header]
    for h in hints:
        for i, c in enumerate(low):
            if c == h:
                return i
    for h in hints:
        for i, c in enumerate(low):
            if h in c:
                return i
    return -1


def parse_blocks(md: str) -> list:
    """Parse markdown into an ordered list of heading / table / text blocks."""
    blocks: list = []
    lines = md.splitlines()
    i = 0
    text_buf: list[str] = []

    def flush_text():
        if text_buf:
            raw = "\n".join(text_buf).strip("\n")
            if raw.strip():
                blocks.append(("text", raw))
            text_buf.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_text()
            blocks.append(("heading", len(m.group(1)), m.group(2).strip()))
            i += 1
            continue

        # Table: a '|' line immediately followed by a separator row
        if stripped.startswith("|") and i + 1 < len(lines) and _SEP_RE.match(lines[i + 1].strip()) \
                and "|" in lines[i + 1]:
            flush_text()
            header = _split_row(stripped)
            align = lines[i + 1].strip()
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append(("table", header, align, rows))
            continue

        text_buf.append(line)
        i += 1

    flush_text()
    return blocks


def _table_to_md(header: List[str], align: str, rows: List[List[str]]) -> str:
    ncol = len(header)

    def fix(cells: List[str]) -> List[str]:
        cells = list(cells)
        if len(cells) < ncol:
            cells += ["—"] * (ncol - len(cells))
        elif len(cells) > ncol:
            cells = cells[:ncol]
        return cells

    out = ["| " + " | ".join(header) + " |"]
    if not align or not _SEP_RE.match(align):
        align = "| " + " | ".join(["---"] * ncol) + " |"
    out.append(align)
    for r in rows:
        out.append("| " + " | ".join(fix(r)) + " |")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Numeric helpers for total recomputation
# ─────────────────────────────────────────────────────────────────────────────

def _to_float(cell: str):
    """Parse a numeric table cell. Returns None for em-dash / blank / non-numeric."""
    if cell is None:
        return None
    s = cell.strip().replace(",", "")
    if s in ("", "—", "-", "–", "N/A", "NA"):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def _fmt_num(v: float) -> str:
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}"


def _recompute_summary(detail_header: List[str], detail_rows: List[List[str]]) -> str | None:
    """Rebuild the per-category summary table from the merged detail rows.

    Groups on the detail table's Type/Category column and sums the weight & length
    columns. Returns markdown for ONE summary table with a PROJECT TOTAL row, or None
    if the detail table lacks the columns needed to summarise.
    """
    type_i = _find_col(detail_header, _TYPE_COL_HINTS)
    kg_i = _find_col(detail_header, _WT_KG_HINTS)
    lb_i = _find_col(detail_header, _WT_LB_HINTS)
    len_i = _find_col(detail_header, _LEN_M_HINTS)
    if type_i < 0 or (kg_i < 0 and lb_i < 0):
        return None

    cats: dict[str, dict[str, float]] = {}
    order: list[str] = []
    tot = {"count": 0.0, "kg": 0.0, "lbs": 0.0, "len": 0.0}

    for r in detail_rows:
        if type_i >= len(r):
            continue
        cat = r[type_i].strip() or "—"
        if cat not in cats:
            cats[cat] = {"count": 0.0, "kg": 0.0, "lbs": 0.0, "len": 0.0}
            order.append(cat)
        c = cats[cat]
        c["count"] += 1
        tot["count"] += 1
        if kg_i >= 0 and kg_i < len(r):
            v = _to_float(r[kg_i])
            if v:
                c["kg"] += v
                tot["kg"] += v
        if lb_i >= 0 and lb_i < len(r):
            v = _to_float(r[lb_i])
            if v:
                c["lbs"] += v
                tot["lbs"] += v
        if len_i >= 0 and len_i < len(r):
            v = _to_float(r[len_i])
            if v:
                c["len"] += v
                tot["len"] += v

    header = ["Category", "Member Count", "Total Length (m)", "Est Total Wt (kg)",
              "Est Total Wt (lbs)", "Est Total Wt (t)"]
    rows: list[list[str]] = []
    for cat in order:
        c = cats[cat]
        rows.append([
            cat,
            _fmt_num(c["count"]),
            _fmt_num(round(c["len"], 1)) if c["len"] else "—",
            _fmt_num(round(c["kg"], 1)) if c["kg"] else "—",
            _fmt_num(round(c["lbs"])) if c["lbs"] else "—",
            _fmt_num(round(c["kg"] / 1000.0, 3)) if c["kg"] else "—",
        ])
    rows.append([
        "PROJECT TOTAL",
        _fmt_num(tot["count"]),
        _fmt_num(round(tot["len"], 1)) if tot["len"] else "—",
        _fmt_num(round(tot["kg"], 1)) if tot["kg"] else "—",
        _fmt_num(round(tot["lbs"])) if tot["lbs"] else "—",
        _fmt_num(round(tot["kg"] / 1000.0, 3)) if tot["kg"] else "—",
    ])
    align = "| " + " | ".join(["---"] * len(header)) + " |"
    return _table_to_md(header, align, rows)


_MARK_HINTS = ("mark/tag", "mark", "tag")
_PROFILE_HINTS = ("profile/section", "profile", "size/section", "section")


def _detail_totals(detail_header: List[str], detail_rows: List[List[str]]) -> dict | None:
    """Compute the project weight totals + heaviest piece from the merged detail table.

    These drive the narrative summary lines so they can never disagree with the table.
    """
    kg_i = _find_col(detail_header, _WT_KG_HINTS)
    lb_i = _find_col(detail_header, _WT_LB_HINTS)
    mark_i = _find_col(detail_header, _MARK_HINTS)
    prof_i = _find_col(detail_header, _PROFILE_HINTS)
    if kg_i < 0:
        return None

    total_kg = 0.0
    total_lbs = 0.0
    best_kg = -1.0
    best_desc = None
    for r in detail_rows:
        kg = _to_float(r[kg_i]) if kg_i < len(r) else None
        if kg:
            total_kg += kg
            if kg > best_kg:
                best_kg = kg
                mark = r[mark_i] if 0 <= mark_i < len(r) and r[mark_i] else "—"
                prof = r[prof_i] if 0 <= prof_i < len(r) and r[prof_i] else "—"
                lbs = _to_float(r[lb_i]) if (0 <= lb_i < len(r)) else None
                lbs = lbs if lbs else kg * 2.20462
                best_desc = f"{mark} | {prof} | {kg:,.1f} kg | {round(lbs):,} lbs"
        if 0 <= lb_i < len(r):
            v = _to_float(r[lb_i])
            if v:
                total_lbs += v
    if not total_lbs and total_kg:
        total_lbs = total_kg * 2.20462
    return {"kg": total_kg, "lbs": total_lbs, "largest": best_desc}


_TONNAGE_NOTE = (
    "_Note: the totals above are summed directly from the take-off table. If any drawing "
    "referenced on the sheets was not uploaded, this take-off is incomplete — upload the "
    "full drawing set (beam schedules, base-plate plans, framing plans) for the complete "
    "tonnage._"
)

# Phrases that mark a take-off TOTAL line whose number must equal the merged table.
_TOTAL_LINE_HINTS = (
    "project tonnage", "combined weight", "computed mto tonnage",
    "total estimated weight", "total weight", "total fabricated tonnage",
    "total project weight", "grand total",
)


def _refresh_narrative_totals(txt: str, n: dict | None) -> str:
    """Rewrite the model's narrative total lines to match the merged take-off table.

    Robust to the unit the model chose (short tons / lbs / kg / metric t) — it reads the
    unit from each line and substitutes the matching computed figure. The model's
    "tonnage discrepancy" note (which embeds the old per-batch number and would now read
    backwards) is replaced with an accurate completeness note.
    """
    if not n or not n.get("kg"):
        return txt
    kg = n["kg"]
    lbs = n.get("lbs") or kg * 2.20462
    t = kg / 1000.0
    short = lbs / 2000.0
    largest = n.get("largest")

    def pick(unit_text: str) -> str:
        u = unit_text.lower()
        if "short ton" in u:
            return f"{short:,.2f}"
        if "lb" in u:
            return f"{lbs:,.0f}"
        if "kg" in u:
            return f"{kg:,.1f}"
        return f"{t:,.2f}"  # metric tonnes / "tonnage"

    out: list[str] = []
    note_done = False
    for line in txt.splitlines():
        low = line.lower()
        if "largest single item by weight" in low and largest:
            out.append(re.sub(
                r"(largest\s+single\s+item\s+by\s+weight[^:\n]*:\s*).*",
                lambda m: f"{m.group(1)}{largest}", line, flags=re.I,
            ))
        elif "discrepancy" in low or ("take-off total of" in low and "cross-check" in low):
            # Premise no longer holds once the total is corrected — replace with a clean note.
            if not note_done:
                out.append(_TONNAGE_NOTE)
                note_done = True
        elif any(k in low for k in _TOTAL_LINE_HINTS):
            out.append(re.sub(
                r"(:\s*)([\d.,]+)", lambda m: f"{m.group(1)}{pick(low)}", line, count=1, flags=re.I,
            ))
        else:
            out.append(line)
    return "\n".join(out)


def normalize_section_headings(md: str) -> str:
    """Promote 'OUTPUT N …' / 'SECTION N …' labels to markdown headings.

    The model is inconsistent about heading markup, which leaves whole sections — most
    importantly OUTPUT 2, the full member take-off with every beam and column — buried in
    body text and missing from the report's navigation. This makes each part a real,
    navigable heading. Consecutive duplicate headings are collapsed.
    """
    if not md:
        return md
    out: list[str] = []
    last_heading = None
    for line in md.splitlines():
        s = line.strip()
        core = s.strip("*").strip()
        is_section = (
            re.match(r"^(OUTPUT|SECTION|PART)\s+\d+\b", core, re.I)
            and not s.startswith("#")
            and not s.startswith("|")
        )
        if is_section:
            heading = "## " + core
            norm = re.sub(r"\s+", " ", core.lower())
            if norm == last_heading:
                continue  # drop a duplicated label line
            last_heading = norm
            out.append(heading)
        else:
            if s.startswith("#"):
                last_heading = re.sub(r"\s+", " ", s.lstrip("#").strip().lower())
            elif s:
                last_heading = None
            out.append(line)
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def merge_reports(outputs: List[str]) -> str:
    """Fuse per-batch markdown reports into ONE complete report (lossless).

    Strategy
    --------
    1. Parse every batch into ordered blocks.
    2. Use batch #1 as the structural skeleton (every batch ran the same mode, so the
       section structure is identical).
    3. While walking the skeleton:
         • A table is replaced by the fusion of ALL batches' tables that share its
           column-header signature (rows concatenated, in batch order — lossless).
         • The per-category SUMMARY table is recomputed from the merged detail table.
         • Each unique table signature is emitted only once.
    4. Any table signature that appears in later batches but NOT in batch #1 is
       appended at the end (still lossless — nothing is dropped).
    5. "Total pieces extracted: N" lines are refreshed to the merged row count.
    """
    outputs = [o for o in (outputs or []) if o and o.strip()]
    if not outputs:
        return ""
    if len(outputs) == 1:
        return outputs[0]

    try:
        return _merge(outputs)
    except Exception as exc:  # noqa: BLE001 — never lose data on a merge bug
        logger.warning("deterministic_merge_failed error=%s — using plain concat", exc)
        return "\n\n".join(o.strip() for o in outputs)


def _iter_sections(blocks: list):
    """Split a block list into (heading_block | None, [content_blocks]) sections.

    A section runs from one heading up to (not including) the next heading. Content
    before the first heading is a preamble section with heading None.
    """
    sections: list = []
    cur_head = None
    cur: list = []
    started = False
    for b in blocks:
        if b[0] == "heading":
            if started or cur:
                sections.append((cur_head, cur))
            cur_head = b
            cur = []
            started = True
        else:
            cur.append(b)
    if cur_head is not None or cur:
        sections.append((cur_head, cur))
    return sections


def _merge(outputs: List[str]) -> str:
    parsed = [parse_blocks(o) for o in outputs]

    # ── 1. Global table fusion by header signature (lossless row concatenation) ──
    fused: dict[Tuple[str, ...], dict] = {}
    first_seen: list[Tuple[str, ...]] = []
    for doc in parsed:
        for blk in doc:
            if blk[0] != "table":
                continue
            _, header, align, rows = blk
            sig = _sig(header)
            if sig not in fused:
                fused[sig] = {"header": header, "align": align, "rows": []}
                first_seen.append(sig)
            fused[sig]["rows"].extend(rows)

    # Principal DETAIL table drives the recomputed summary + total-pieces refresh.
    detail_sig = None
    best = -1
    for sig in first_seen:
        f = fused[sig]
        score = len(f["rows"]) + (100_000 if _header_has(f["header"], _DETAIL_HEADER_HINTS) else 0)
        if score > best:
            best = score
            detail_sig = sig
    recomputed_summary = None
    narrative_totals = None
    if detail_sig is not None:
        d = fused[detail_sig]
        recomputed_summary = _recompute_summary(d["header"], d["rows"])
        narrative_totals = _detail_totals(d["header"], d["rows"])
    merged_detail_count = len(fused[detail_sig]["rows"]) if detail_sig is not None else 0

    # ── 2. Section buckets keyed by heading text, in first-seen order ──
    # Every batch runs the same mode, so section headings repeat across batches; we
    # collapse them into ONE section whose content is the union of all batches'.
    order: list[str] = []
    sec: dict[str, dict] = {}
    for doc in parsed:
        for head, content in _iter_sections(doc):
            key = _norm_cell(head[2]).lower() if head else "__preamble__"
            if key not in sec:
                sec[key] = {"head": head, "blocks": []}
                order.append(key)
            sec[key]["blocks"].extend(content)

    emitted_tables: set[Tuple[str, ...]] = set()
    summary_done = [False]
    out_parts: list[str] = []

    def emit_table(sig: Tuple[str, ...]):
        f = fused[sig]
        if _header_has(f["header"], _SUMMARY_HEADER_HINTS) and recomputed_summary:
            if not summary_done[0]:
                out_parts.append(recomputed_summary)
                summary_done[0] = True
            return
        out_parts.append(_table_to_md(f["header"], f["align"], f["rows"]))

    # ── 3. Emit each merged section once: heading, then de-duplicated prose +
    #        fused tables (so RFIs / findings from EVERY batch survive) ──
    for key in order:
        s = sec[key]
        if s["head"] is not None:
            out_parts.append("#" * s["head"][1] + " " + s["head"][2])
        seen_prose: set[str] = set()
        for blk in s["blocks"]:
            if blk[0] == "table":
                sig = _sig(blk[1])
                if sig in emitted_tables:
                    continue
                emitted_tables.add(sig)
                emit_table(sig)
            else:  # text
                txt = blk[1]
                if merged_detail_count:
                    # Refresh per-batch counts to the merged total BEFORE dedup, so
                    # otherwise-identical count lines collapse to one.
                    txt = _TOTAL_PIECES_RE.sub(
                        lambda m: f"{m.group(1)}{merged_detail_count:,}", txt
                    )
                # Rewrite the take-off's own tonnage/weight lines to match the merged
                # table, so a per-batch narrative figure can never contradict it.
                txt = _refresh_narrative_totals(txt, narrative_totals)
                norm = re.sub(r"\s+", " ", txt.strip()).lower()
                if not norm or norm in seen_prose:
                    continue
                seen_prose.add(norm)
                out_parts.append(txt)

    # ── 4. Safety net: emit any fused table never reached above (lose nothing) ──
    for sig in first_seen:
        if sig in emitted_tables:
            continue
        emitted_tables.add(sig)
        emit_table(sig)

    merged = "\n\n".join(p for p in out_parts if p is not None and p != "")
    logger.info(
        "deterministic_merge_complete batches=%d sections=%d unique_tables=%d detail_rows=%d",
        len(outputs), len(order), len(first_seen), merged_detail_count,
    )
    return merged
