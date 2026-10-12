"""Generate export artefacts (Word / PDF / Excel / CSV / Markdown) from analysis output.

Each format is built on its own and written to a temporary file that is renamed into
place, so a download never sees a half-written file and one slow or failing format never
blocks the others. Builders are linear in report size: a 300-row, 16-column take-off
renders in seconds in every format.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config import settings

logger = logging.getLogger(__name__)

NAVY = colors.HexColor("#0d2240")
GOLD = colors.HexColor("#f5a800")
INK = colors.HexColor("#1a2d44")
MUTED = colors.HexColor("#6b8299")
LINE = colors.HexColor("#e2eaf2")

NAVY_RGB = RGBColor(0x0D, 0x22, 0x40)
INK_RGB = RGBColor(0x1A, 0x2D, 0x44)
WHITE_RGB = RGBColor(0xFF, 0xFF, 0xFF)

# Tables wider than this switch Word and PDF exports to landscape with a denser font.
WIDE_COLS = 7

EXPORT_DIR = Path(settings.upload_dir) / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*]|\d+[.)])\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
_PIPE_RE = re.compile(r"(?<!\\)\|")


def _lines(md: str) -> list[str]:
    # Split on newlines only: str.splitlines() also breaks on \x0b, \x1c, \u2028 …, and one
    # such character inside a cell would end a table.
    return md.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _split_blocks(md: str) -> list[tuple[str, str]]:
    """Return (kind, text) blocks: 'h1' 'h2' 'h3' 'table' 'list' 'p'.

    'list' blocks keep their original lines (indent + marker), so numbering and nesting
    survive; read them with _list_items().
    """
    blocks: list[tuple[str, str]] = []
    lines = _lines(md)
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append((f"h{min(len(heading.group(1)), 3)}", heading.group(2).strip()))
            i += 1
        elif line.startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            tbl = [line]
            i += 2  # skip separator
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(tbl)))
        elif _LIST_RE.match(line):
            items = []
            while i < len(lines) and _LIST_RE.match(lines[i]):
                items.append(lines[i].rstrip())
                i += 1
            blocks.append(("list", "\n".join(items)))
        else:
            para = [line]
            i += 1
            while (i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|"))
                   and not _LIST_RE.match(lines[i])):
                para.append(lines[i])
                i += 1
            blocks.append(("p", " ".join(para)))
    return blocks


def _list_items(text: str) -> list[tuple[int, str, str]]:
    """(level, marker, text) per item; marker is '•' for bullets or the source number ('3.')."""
    parsed = []
    for line in text.split("\n"):
        m = _LIST_RE.match(line)
        if m:
            parsed.append((len(m.group(1).expandtabs(4)), m.group(2), m.group(3)))
    indents = sorted({ind for ind, _, _ in parsed})
    return [(min(indents.index(ind), 3), "•" if mk in ("-", "*") else mk, body) for ind, mk, body in parsed]


def _list_line(level: int, marker: str, body: str) -> str:
    return "   " * level + f"{marker} {_text(body)}"


def _table_rows(tbl_md: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in _lines(tbl_md):
        row = line.strip()
        if not row.startswith("|"):
            continue
        row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        # GFM escapes a literal pipe inside a cell as "\|".
        rows.append([c.strip().replace("\\|", "|") for c in _PIPE_RE.split(row)])
    return _normalize_rows(rows)


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    """Force every row to the header's column count.

    Word (python-docx) and PDF (reportlab) BOTH crash if a data row has a different number
    of cells than the header — which happens whenever a cell contains an unescaped '|'
    (e.g. pipe-separated Flag codes like "RECONCILE | VIF"). Short rows are padded; rows
    with extra cells have the overflow merged back into the last column so no data is lost.
    """
    if not rows:
        return rows
    ncols = max(1, len(rows[0]))
    fixed: list[list[str]] = []
    for r in rows:
        if len(r) == ncols:
            fixed.append(r)
        elif len(r) < ncols:
            fixed.append(r + [""] * (ncols - len(r)))
        else:
            fixed.append(r[: ncols - 1] + [" | ".join(r[ncols - 1:])])
    return fixed


# Emphasis markers only where Markdown treats them as emphasis, so arithmetic such as
# "2*3*4" or "12 * 26 * 3.5" keeps its operators.
_BOLD_RE = re.compile(r"(?<![\w*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![\w*])")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")
_CODE_RE = re.compile(r"`([^`]+)`")


def _strip_md(text: str) -> str:
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    return _CODE_RE.sub(r"\1", text)


# Characters XML (Word, Excel) cannot store; they occasionally arrive in drawing text.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(s: str | None) -> str:
    """Markdown-stripped text that is safe to put in a Word/Excel/PDF cell."""
    return _CTRL_RE.sub("", _strip_md(s or ""))


def _is_wide(blocks: list[tuple[str, str]]) -> bool:
    for kind, text in blocks:
        if kind == "table":
            first = text.split("\n", 1)[0]
            if first.strip().strip("|").count("|") + 1 > WIDE_COLS:
                return True
    return False


@contextmanager
def _atomic(path: Path):
    """Yield a temp path next to `path`; move it into place only if the build succeeds."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


_INVALID_SHEET = set(r"\/?*[]:")


def _safe_sheet_name(name: str, used: set[str]) -> str:
    """Return an Excel-legal (≤31 char, unique) worksheet name."""
    cleaned = "".join("_" if c in _INVALID_SHEET else c for c in (name or "")).strip()
    cleaned = cleaned[:31] or "Table"
    base = cleaned
    n = 2
    while cleaned.lower() in used:
        suffix = f" ({n})"
        cleaned = (base[: 31 - len(suffix)] + suffix).strip()
        n += 1
    used.add(cleaned.lower())
    return cleaned


# A "Table: Member Take-Off" line just above a table names it (the engine writes these).
_CAPTION_RE = re.compile(r"^\s*(?:\*\*)?Table:\s*(.+?)(?:\*\*)?\s*$", re.IGNORECASE)


# ---------- MARKDOWN ----------
def export_markdown(content: str, meta: dict) -> str:
    path = EXPORT_DIR / f"{meta['id']}.md"
    header = (
        f"# {meta['mode_label']}\n"
        + (f"**Report:** {meta['title']}  \n" if meta.get("title") else "")
        + f"**Project:** {meta.get('project_name', 'Quick Analysis')}  \n"
        f"**Generated:** {meta.get('completed_at', '')}  \n"
        f"**Model:** {meta.get('model_used', '')}  \n"
        f"**Hash:** `{meta.get('blockchain_hash', '')}`\n\n---\n\n"
    )
    with _atomic(path) as tmp:
        tmp.write_text(header + content, encoding="utf-8")
    return str(path)


# ---------- CSV ----------
def _csv_safe(value: str) -> str:
    """Spreadsheets run a cell that starts with = + - @ as a formula; keep it as text."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_csv(content: str, meta: dict) -> str:
    path = EXPORT_DIR / f"{meta['id']}.csv"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "type", "content"])
    for kind, text in _split_blocks(content):
        if kind == "table":
            for row in _table_rows(text):
                w.writerow(["table-row", "row", _csv_safe(" | ".join(_text(c) for c in row))])
        elif kind == "list":
            for item in _list_items(text):
                w.writerow(["list", "item", _csv_safe(_list_line(*item))])
        else:
            w.writerow([kind, kind, _csv_safe(_text(text).replace("\n", " "))])
    with _atomic(path) as tmp:
        # BOM so Excel opens UTF-8 (×, —, ″) correctly.
        tmp.write_text(buf.getvalue(), encoding="utf-8-sig")
    return str(path)


# ---------- EXCEL ----------
# Plain numbers ("1,234.5", "12", "0.75") become real numbers so columns can be summed.
_NUM_RE = re.compile(r"^-?(?:0|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)(?:\.\d+)?$")


def _xl_value(raw: str) -> tuple[object, str | None]:
    """(value, number_format): plain numbers become numbers that display exactly as written."""
    t = _text(raw)
    # Excel keeps 15 significant digits; longer digit strings (IDs, heat numbers) stay text.
    if not _NUM_RE.match(t) or sum(ch.isdigit() for ch in t) > 15:
        return t, None
    digits = t.replace(",", "")
    grouped = "," in t
    if "." in digits:
        places = len(digits.split(".", 1)[1])
        return float(digits), ("#,##0." if grouped else "0.") + "0" * places
    return int(digits), ("#,##0" if grouped else None)


def _xl_set(cell, value) -> None:
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"  # text that merely starts with "=" must not become a formula


def export_xlsx(content: str, meta: dict) -> str:
    """Excel export built for scale.

    Narrative (headings, paragraphs, lists) lands on a 'Report' sheet. EVERY table —
    including a multi-thousand-row MTO — gets its OWN worksheet with a frozen, filtered
    header row, so the full take-off is browsable and exportable with no row cap.
    """
    path = EXPORT_DIR / f"{meta['id']}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="0D2240")

    _xl_set(ws["A1"], _text(meta.get("title") or meta["mode_label"]))
    ws["A1"].font = Font(name="Calibri", bold=True, size=16, color="0D2240")
    cover = [meta["mode_label"] if meta.get("title") else None,
             f"Project: {meta.get('project_name', 'Quick Analysis')}",
             f"Generated: {meta.get('completed_at', '')}",
             f"Model: {meta.get('model_used', '')}",
             f"Hash: {meta.get('blockchain_hash', '')}"]
    row = 2
    for line in cover:
        if line:
            _xl_set(ws.cell(row=row, column=1), _text(line))
            row += 1
    ws.column_dimensions["A"].width = 80

    used_sheets = {"report"}
    row += 1
    last_heading = None
    caption = None
    table_idx = 0

    for kind, text in _split_blocks(content):
        if kind in ("h1", "h2", "h3"):
            last_heading = _text(text)
            caption = None
            cell = ws.cell(row=row, column=1)
            _xl_set(cell, last_heading)
            cell.font = Font(bold=True, size=13 if kind == "h1" else 12, color="0D2240")
            row += 1
        elif kind == "table":
            rows = _table_rows(text)
            if not rows:
                continue
            table_idx += 1
            title = caption or last_heading or f"Table {table_idx}"
            caption = None
            sheet_name = _safe_sheet_name(title, used_sheets)
            tws = wb.create_sheet(sheet_name)
            _write_table_sheet(tws, rows, header_font, header_fill)
            # Cross-reference on the Report sheet.
            ref = ws.cell(row=row, column=1)
            _xl_set(ref, f"▸ {title} → sheet “{sheet_name}” ({len(rows) - 1:,} rows)")
            ref.font = Font(italic=True, color="0D2240")
            row += 1
        elif kind == "list":
            caption = None
            for item in _list_items(text):
                _xl_set(ws.cell(row=row, column=1), _list_line(*item))
                row += 1
        else:
            m = _CAPTION_RE.match(text)
            caption = _text(m.group(1)) if m else None
            _xl_set(ws.cell(row=row, column=1), _text(text))
            row += 1

    with _atomic(path) as tmp:
        wb.save(str(tmp))
    return str(path)


def _write_table_sheet(tws, rows: list[list[str]], header_font, header_fill) -> None:
    """Render one markdown table onto its own worksheet: styled, frozen, filtered."""
    header = rows[0]
    ncol = len(header)
    for c, h in enumerate(header, start=1):
        cell = tws.cell(row=1, column=c)
        _xl_set(cell, _text(h))
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for ri, r in enumerate(rows[1:], start=2):
        for c in range(1, ncol + 1):
            cell = tws.cell(row=ri, column=c)
            value, number_format = _xl_value(r[c - 1]) if c - 1 < len(r) else ("", None)
            _xl_set(cell, value)
            if number_format:
                cell.number_format = number_format

    # Column widths from a sample of the first rows (cheap on huge tables).
    sample = rows[: min(len(rows), 60)]
    for c in range(ncol):
        width = max((len(_strip_md(rw[c])) for rw in sample if c < len(rw)), default=10)
        tws.column_dimensions[tws.cell(row=1, column=c + 1).column_letter].width = min(max(width + 2, 10), 48)

    tws.freeze_panes = "A2"
    if len(rows) > 1 and ncol > 0:
        last_col = tws.cell(row=1, column=ncol).column_letter
        tws.auto_filter.ref = f"A1:{last_col}{len(rows)}"


# ---------- WORD ----------
def _docx_landscape(doc) -> None:
    for sec in doc.sections:
        if sec.orientation != WD_ORIENT.LANDSCAPE:
            sec.orientation = WD_ORIENT.LANDSCAPE
            sec.page_width, sec.page_height = sec.page_height, sec.page_width
        sec.left_margin = sec.right_margin = Inches(0.5)
        sec.top_margin = sec.bottom_margin = Inches(0.6)


def _docx_table(doc, rows: list[list[str]]) -> None:
    """Add a styled table in one linear pass.

    python-docx's Table.cell(r, c) rebuilds the whole cell list on every call, which made a
    300-row take-off take minutes; walking rows and their cells directly is linear.
    """
    ncol = len(rows[0])
    size = Pt(7 if ncol > 12 else 8 if ncol > WIDE_COLS else 10)
    tbl = doc.add_table(rows=len(rows), cols=ncol)
    try:
        tbl.style = "Table Grid"
    except (KeyError, ValueError):
        pass
    for ri, (row, data) in enumerate(zip(tbl.rows, rows)):
        if ri == 0:
            repeat = OxmlElement("w:tblHeader")  # header row repeats on every page
            repeat.set(qn("w:val"), "true")
            row._tr.get_or_add_trPr().append(repeat)
        for ci, cell in enumerate(row.cells):
            run = cell.paragraphs[0].add_run(_text(data[ci]) if ci < len(data) else "")
            run.font.size = size
            if ri == 0:
                run.font.bold = True
                run.font.color.rgb = WHITE_RGB
                shading = OxmlElement("w:shd")
                shading.set(qn("w:val"), "clear")
                shading.set(qn("w:color"), "auto")
                shading.set(qn("w:fill"), "0D2240")
                cell._tc.get_or_add_tcPr().append(shading)
            else:
                run.font.color.rgb = INK_RGB


def export_docx(content: str, meta: dict) -> str:
    path = EXPORT_DIR / f"{meta['id']}.docx"
    blocks = _split_blocks(content)
    doc = Document()
    if _is_wide(blocks):
        _docx_landscape(doc)

    # Cover
    doc.add_paragraph("4XSTRUCT · STRUCTMIND").runs[0].font.size = Pt(10)
    title = doc.add_paragraph()
    tr = title.add_run(_text(meta["mode_label"]).upper())
    tr.bold = True
    tr.font.size = Pt(28)
    tr.font.color.rgb = NAVY_RGB
    if meta.get("title"):
        doc.add_paragraph(_text(f"Report: {meta['title']}"))
    doc.add_paragraph(_text(f"Project: {meta.get('project_name', 'Quick Analysis')}"))
    doc.add_paragraph(_text(f"Generated: {meta.get('completed_at', '')}"))
    doc.add_paragraph(_text(f"Model: {meta.get('model_used', '')}"))
    doc.add_paragraph(_text(f"SHA-256 Hash: {meta.get('blockchain_hash', '')}"))
    doc.add_paragraph("")

    for kind, text in blocks:
        if kind in ("h1", "h2", "h3"):
            lvl = {"h1": 1, "h2": 2, "h3": 3}[kind]
            h = doc.add_heading(_text(text), level=lvl)
            for run in h.runs:
                run.font.color.rgb = NAVY_RGB
        elif kind == "table":
            rows = _table_rows(text)
            if not rows:
                continue
            _docx_table(doc, rows)
            doc.add_paragraph("")
        elif kind == "list":
            for level, marker, body in _list_items(text):
                if marker == "•":
                    style = "List Bullet" if level == 0 else f"List Bullet {min(level + 1, 3)}"
                    doc.add_paragraph(_text(body), style=style)
                else:
                    # Source numbers written as text: Word's shared auto-numbering would run
                    # on across every list in the document.
                    para = doc.add_paragraph(f"{marker}\t{_text(body)}")
                    para.paragraph_format.left_indent = Inches(0.35 + 0.3 * level)
                    para.paragraph_format.first_line_indent = Inches(-0.3)
        else:
            doc.add_paragraph(_text(text))

    with _atomic(path) as tmp:
        doc.save(str(tmp))
    return str(path)


# ---------- PDF ----------
# A Unicode TrueType font keeps ×, ≈, ≥, Σ, ″ intact; the built-in Helvetica covers only
# Windows-1252, so without one those characters are transliterated instead.
_FONT_CANDIDATES = (
    (os.environ.get("PDF_FONT_REGULAR", ""), os.environ.get("PDF_FONT_BOLD", "")),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)

_ASCII_FALLBACK = {
    "Σ": "Sum ", "≈": "~", "≥": ">=", "≤": "<=", "≠": "!=", "±": "+/-", "→": "->", "←": "<-",
    "↔": "<->", "▸": ">", "►": ">", "✓": "OK", "✔": "OK", "✗": "x", "✘": "x", "⚠": "!",
    "″": '"', "′": "'", "−": "-", "√": "sqrt", "∅": "dia.", "⌀": "dia.", "Ø": "dia.",
    "✅": "OK", "❌": "X", "⛔": "X", "🔴": "*", "🟠": "*", "🟡": "*", "🟢": "*", "🔵": "*",
    "⭐": "*", "📌": "*",
}
_INVISIBLE_RE = re.compile("[\ufe0e\ufe0f\u200d]")  # emoji variation selectors, joiners


@lru_cache(maxsize=1)
def _pdf_fonts() -> tuple[str, str, frozenset | None]:
    """(regular, bold, glyph coverage) — registered once per process.

    Coverage is the set of code points the Unicode font can draw, or None when falling
    back to Helvetica (Windows-1252 only).
    """
    for regular, bold in _FONT_CANDIDATES:
        if regular and bold and os.path.exists(regular) and os.path.exists(bold):
            try:
                face = TTFont("SM-Sans", regular)
                pdfmetrics.registerFont(face)
                pdfmetrics.registerFont(TTFont("SM-Sans-Bold", bold))
                pdfmetrics.registerFontFamily("SM-Sans", normal="SM-Sans", bold="SM-Sans-Bold",
                                              italic="SM-Sans", boldItalic="SM-Sans-Bold")
                return "SM-Sans", "SM-Sans-Bold", frozenset(face.face.charToGlyph)
            except Exception:  # noqa: BLE001 — unreadable font file: try the next one
                logger.warning("pdf_font_unusable path=%s", regular)
    return "Helvetica", "Helvetica-Bold", None


def _pdf_text(s: str | None, coverage: frozenset | None, limit: int | None = None) -> str:
    """Plain text → reportlab Paragraph markup: '<' and '&' escaped, only glyphs the font has."""
    t = _INVISIBLE_RE.sub("", _text(s))
    if limit and len(t) > limit:
        t = t[: limit - 1] + "…"
    if coverage is None:
        t = "".join(_ASCII_FALLBACK.get(ch, ch) for ch in t)
        t = t.encode("cp1252", "replace").decode("cp1252")
    else:
        t = "".join(ch if ord(ch) in coverage or ch == "\n" else _ASCII_FALLBACK.get(ch, "") for ch in t)
    return _xml_escape(t)


def _col_widths(rows: list[list[str]], avail: float, regular: str, bold: str, fs: float,
                pad: float = 7.0) -> list[float]:
    """Share the page width so every column fits and no word is broken mid-word.

    Each column first gets the width of its longest single word (header words in bold);
    the space left over goes to the columns whose full text would still wrap, in
    proportion to how much more they need.
    """
    ncol = len(rows[0])
    sample = rows[:200]
    cap = avail * 0.3  # one runaway token (a URL, a long code) must not starve the rest
    need_min, need_full = [], []
    for c in range(ncol):
        head = _text(rows[0][c]) if c < len(rows[0]) else ""
        cells = [_text(r[c]) for r in sample[1:] if c < len(r)]
        words = [w for x in cells for w in x.split()] or [""]
        widest_word = max(pdfmetrics.stringWidth(w, regular, fs) for w in words)
        widest_head = max((pdfmetrics.stringWidth(w, bold, fs) for w in head.split()), default=0)
        widest_line = max((pdfmetrics.stringWidth(x[:60], regular, fs) for x in cells), default=0)
        lo = min(max(widest_word, widest_head) + pad, cap)
        need_min.append(lo)
        need_full.append(max(lo, min(widest_line + pad, cap)))
    total_min = sum(need_min)
    if total_min >= avail:  # too many columns: shrink proportionally (some wrapping is unavoidable)
        return [avail * w / total_min for w in need_min]
    extra = avail - total_min
    wants = [f - m for f, m in zip(need_full, need_min)]
    total_want = sum(wants)
    if total_want <= extra:  # everything fits on one line; spread the spare room evenly
        spare = (extra - total_want) / ncol
        return [f + spare for f in need_full]
    return [m + extra * w / total_want for m, w in zip(need_min, wants)]


# Rows split across pages, so long cells are fine; this only bounds pathological input.
_PDF_CELL_LIMIT = 20000


def export_pdf(content: str, meta: dict) -> str:
    path = EXPORT_DIR / f"{meta['id']}.pdf"
    blocks = _split_blocks(content)
    wide = _is_wide(blocks)
    regular, bold, coverage = _pdf_fonts()
    uni = coverage is not None
    styles = getSampleStyleSheet()

    def p(s, **kw):
        return _pdf_text(s, coverage, **kw)

    story = []
    brand = ParagraphStyle("brand", parent=styles["Normal"], fontName=bold, textColor=GOLD,
                           fontSize=10, spaceAfter=6)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName=bold, textColor=NAVY,
                        fontSize=22, leading=26, spaceAfter=14)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=bold, textColor=NAVY,
                        fontSize=16, leading=20, spaceAfter=10)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontName=bold, textColor=NAVY,
                        fontSize=13, leading=16, spaceAfter=8)
    body = ParagraphStyle("body", parent=styles["Normal"], fontName=regular, textColor=INK,
                          fontSize=10.5, leading=15, spaceAfter=8)
    meta_s = ParagraphStyle("meta", parent=body, textColor=MUTED, fontSize=9, spaceAfter=4)
    list_styles: dict[int, ParagraphStyle] = {}

    story.append(Paragraph("4XSTRUCT · STRUCTMIND" if uni else "4XSTRUCT - STRUCTMIND", brand))
    story.append(Paragraph(p(meta["mode_label"]).upper(), h1))
    if meta.get("title"):
        story.append(Paragraph(p(f"Report: {meta['title']}"), meta_s))
    story.append(Paragraph(p(f"Project: {meta.get('project_name', 'Quick Analysis')}"), meta_s))
    story.append(Paragraph(p(f"Generated: {meta.get('completed_at', '')}"), meta_s))
    story.append(Paragraph(p(f"Model: {meta.get('model_used', '')}"), meta_s))
    story.append(Paragraph(p(f"SHA-256: {meta.get('blockchain_hash', '')}"), meta_s))
    story.append(Spacer(1, 0.2 * inch))

    pagesize = landscape(LETTER) if wide else LETTER
    side = 0.5 * inch if wide else 0.75 * inch
    avail = pagesize[0] - 2 * side

    tbl_style = TableStyle(
        [
            ("BACKGROUND",     (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR",      (0, 0), (-1, 0), colors.white),
            ("LEFTPADDING",    (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",   (0, 0), (-1, -1), 3),
            ("TOPPADDING",     (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
            ("GRID",           (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN",         (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
        ]
    )

    for kind, text in blocks:
        if kind == "h1":
            story.append(Paragraph(p(text), h1))
        elif kind == "h2":
            story.append(Paragraph(p(text), h2))
        elif kind == "h3":
            story.append(Paragraph(p(text), h3))
        elif kind == "table":
            rows = _table_rows(text)
            if not rows:
                continue
            header = rows[0]
            body_rows = rows[1:] or [[""] * len(header)]
            ncol = max(1, len(header))
            fs = 6.5 if ncol > 12 else 7.5 if ncol > WIDE_COLS else 9
            cell = ParagraphStyle("cell", parent=body, fontSize=fs, leading=fs + 2, spaceAfter=0)
            head_cell = ParagraphStyle("headcell", parent=cell, fontName=bold, textColor=colors.white)
            widths = _col_widths(rows, avail, regular, bold, fs)
            header_cells = [Paragraph(p(c), head_cell) for c in header]
            # Chunk very large tables: each chunk re-prints the header and keeps
            # repeatRows so the build stays fast and the header is always visible.
            CHUNK = 80
            for start in range(0, len(body_rows), CHUNK):
                chunk = body_rows[start:start + CHUNK]
                data = [list(header_cells)] + [
                    [Paragraph(p(c, limit=_PDF_CELL_LIMIT), cell) for c in r] for r in chunk
                ]
                tbl = Table(data, colWidths=widths, repeatRows=1, splitInRow=1)
                tbl.setStyle(tbl_style)
                story.append(tbl)
            story.append(Spacer(1, 0.15 * inch))
        elif kind == "list":
            for level, marker, item in _list_items(text):
                if level not in list_styles:
                    list_styles[level] = ParagraphStyle(f"list{level}", parent=body, leftIndent=14 * (level + 1),
                                                        firstLineIndent=-10, spaceAfter=3)
                story.append(Paragraph(f"{p(marker)}&nbsp;&nbsp;{p(item)}", list_styles[level]))
        else:
            story.append(Paragraph(p(text), body))

    with _atomic(path) as tmp:
        pdf = SimpleDocTemplate(
            str(tmp),
            pagesize=pagesize,
            leftMargin=side,
            rightMargin=side,
            topMargin=0.75 * inch if wide else 0.85 * inch,
            bottomMargin=0.6 * inch if wide else 0.75 * inch,
            title=_text(meta.get("title") or meta.get("project_name") or meta["mode_label"]),
            author="4XStruct",
        )
        pdf.build(story)
    return str(path)


# Fast formats first, so they are on disk even if a later one is slow.
EXPORTERS = {
    "markdown": export_markdown,
    "csv": export_csv,
    "xlsx": export_xlsx,
    "pdf": export_pdf,
    "docx": export_docx,
}

# File extension on disk for each exporter key.
EXTENSIONS = {"markdown": "md", "csv": "csv", "xlsx": "xlsx", "pdf": "pdf", "docx": "docx"}


def remove_exports(aid: str) -> None:
    """Delete an analysis's export files; they are rebuilt on the next download."""
    for ext in EXTENSIONS.values():
        try:
            (EXPORT_DIR / f"{aid}.{ext}").unlink(missing_ok=True)
        except OSError:
            pass


_FILENAME_UNSAFE_RE = re.compile(r"[^\w\-]+")


def download_name(*parts: str) -> str:
    """'Tower C', 'Master MTO Engine' -> 'Tower_C_Master_MTO_Engine' (letters in any script kept)."""
    joined = "_".join(p for p in parts if p)
    return _FILENAME_UNSAFE_RE.sub("_", joined).strip("_") or "report"


def generate_export(fmt: str, content: str, meta: dict) -> str:
    """Build ONE format (raises on failure)."""
    return EXPORTERS[fmt](content, meta)


def generate_all_exports(content: str, meta: dict) -> list[dict]:
    results = []
    for fmt, fn in EXPORTERS.items():
        try:
            p = fn(content, meta)
            results.append({"format": fmt, "path": p})
        except Exception as e:  # noqa: BLE001
            logger.exception("export_failed format=%s id=%s", fmt, meta.get("id"))
            results.append({"format": fmt, "path": "", "error": str(e)})
    return results
