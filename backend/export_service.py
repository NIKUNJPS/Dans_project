"""Generate export artefacts (Word / PDF / Excel / CSV / Markdown) from analysis output."""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config import settings

NAVY = colors.HexColor("#0d2240")
GOLD = colors.HexColor("#f5a800")
INK = colors.HexColor("#1a2d44")
MUTED = colors.HexColor("#6b8299")
LINE = colors.HexColor("#e2eaf2")

EXPORT_DIR = Path(settings.upload_dir) / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _split_blocks(md: str) -> list[tuple[str, str]]:
    """Yield (kind, text) blocks where kind is 'h1' 'h2' 'h3' 'table' 'li' 'p'."""
    blocks: list[tuple[str, str]] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("### "):
            blocks.append(("h3", line[4:].strip()))
            i += 1
        elif line.startswith("## "):
            blocks.append(("h2", line[3:].strip()))
            i += 1
        elif line.startswith("# "):
            blocks.append(("h1", line[2:].strip()))
            i += 1
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            tbl = [line]
            i += 2  # skip separator
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(tbl)))
        elif re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            blocks.append(("li", "\n".join(items)))
        elif re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            blocks.append(("ol", "\n".join(items)))
        else:
            para = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", "- ", "* ")):
                para.append(lines[i])
                i += 1
            blocks.append(("p", " ".join(para)))
    return blocks


def _table_rows(tbl_md: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in tbl_md.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def _strip_md(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


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


# ---------- MARKDOWN ----------
def export_markdown(content: str, meta: dict) -> str:
    fname = f"{meta['id']}.md"
    path = EXPORT_DIR / fname
    header = (
        f"# {meta['mode_label']}\n"
        f"**Project:** {meta.get('project_name', 'Quick Analysis')}  \n"
        f"**Generated:** {meta.get('completed_at', '')}  \n"
        f"**Model:** {meta.get('model_used', '')}  \n"
        f"**Hash:** `{meta.get('blockchain_hash', '')}`\n\n---\n\n"
    )
    path.write_text(header + content, encoding="utf-8")
    return str(path)


# ---------- CSV ----------
def export_csv(content: str, meta: dict) -> str:
    fname = f"{meta['id']}.csv"
    path = EXPORT_DIR / fname
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "type", "content"])
    for kind, text in _split_blocks(content):
        if kind == "table":
            for row in _table_rows(text):
                w.writerow(["table-row", "row", " | ".join(row)])
        else:
            w.writerow([kind, kind, _strip_md(text).replace("\n", " ")])
    path.write_text(buf.getvalue(), encoding="utf-8")
    return str(path)


# ---------- EXCEL ----------
def export_xlsx(content: str, meta: dict) -> str:
    """Excel export built for scale.

    Narrative (headings, paragraphs, lists) lands on a 'Report' sheet. EVERY table —
    including a multi-thousand-row MTO — gets its OWN worksheet with a frozen, filtered
    header row, so the full take-off is browsable and exportable with no row cap.
    """
    fname = f"{meta['id']}.xlsx"
    path = EXPORT_DIR / fname
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="0D2240")

    ws["A1"] = meta["mode_label"]
    ws["A1"].font = Font(name="Calibri", bold=True, size=16, color="0D2240")
    ws["A2"] = f"Project: {meta.get('project_name', 'Quick Analysis')}"
    ws["A3"] = f"Generated: {meta.get('completed_at', '')}"
    ws["A4"] = f"Model: {meta.get('model_used', '')}"
    ws["A5"] = f"Hash: {meta.get('blockchain_hash', '')}"
    ws.column_dimensions["A"].width = 80

    used_sheets = {"report"}
    row = 7
    last_heading = None
    table_idx = 0

    for kind, text in _split_blocks(content):
        if kind in ("h1", "h2", "h3"):
            last_heading = _strip_md(text)
            ws.cell(row=row, column=1, value=last_heading).font = Font(
                bold=True, size=13 if kind == "h1" else 12, color="0D2240"
            )
            row += 1
        elif kind == "table":
            rows = _table_rows(text)
            if not rows:
                continue
            table_idx += 1
            sheet_name = _safe_sheet_name(last_heading or f"Table {table_idx}", used_sheets)
            tws = wb.create_sheet(sheet_name)
            _write_table_sheet(tws, rows, header_font, header_fill)
            # Cross-reference on the Report sheet.
            ref = ws.cell(
                row=row, column=1,
                value=f"▸ {last_heading or f'Table {table_idx}'} → sheet “{sheet_name}” ({len(rows) - 1:,} rows)",
            )
            ref.font = Font(italic=True, color="0D2240")
            row += 1
        elif kind in ("li", "ol"):
            for it in text.splitlines():
                ws.cell(row=row, column=1, value="• " + _strip_md(it))
                row += 1
        else:
            ws.cell(row=row, column=1, value=_strip_md(text))
            row += 1

    wb.save(str(path))
    return str(path)


def _write_table_sheet(tws, rows: list[list[str]], header_font, header_fill) -> None:
    """Render one markdown table onto its own worksheet: styled, frozen, filtered."""
    header = rows[0]
    ncol = len(header)
    for c, h in enumerate(header, start=1):
        cell = tws.cell(row=1, column=c, value=_strip_md(h))
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for ri, r in enumerate(rows[1:], start=2):
        for c in range(1, ncol + 1):
            val = _strip_md(r[c - 1]) if c - 1 < len(r) else ""
            tws.cell(row=ri, column=c, value=val)

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
def export_docx(content: str, meta: dict) -> str:
    fname = f"{meta['id']}.docx"
    path = EXPORT_DIR / fname
    doc = Document()

    # Cover
    doc.add_paragraph("4XSTRUCT · STRUCTMIND").runs[0].font.size = Pt(10)
    title = doc.add_paragraph()
    tr = title.add_run(meta["mode_label"].upper())
    tr.bold = True
    tr.font.size = Pt(28)
    tr.font.color.rgb = RGBColor(0x0D, 0x22, 0x40)
    doc.add_paragraph(f"Project: {meta.get('project_name', 'Quick Analysis')}")
    doc.add_paragraph(f"Generated: {meta.get('completed_at', '')}")
    doc.add_paragraph(f"Model: {meta.get('model_used', '')}")
    doc.add_paragraph(f"SHA-256 Hash: {meta.get('blockchain_hash', '')}")
    doc.add_paragraph("")

    for kind, text in _split_blocks(content):
        if kind in ("h1", "h2", "h3"):
            lvl = {"h1": 1, "h2": 2, "h3": 3}[kind]
            h = doc.add_heading(_strip_md(text), level=lvl)
            for run in h.runs:
                run.font.color.rgb = RGBColor(0x0D, 0x22, 0x40)
        elif kind == "table":
            rows = _table_rows(text)
            if not rows:
                continue
            tbl = doc.add_table(rows=len(rows), cols=len(rows[0]))
            # Manually style: high-contrast navy header + white text (visible)
            from docx.oxml.ns import qn
            from docx.oxml import OxmlElement
            for ci in range(len(rows[0])):
                cell = tbl.cell(0, ci)
                shading = OxmlElement("w:shd")
                shading.set(qn("w:val"), "clear")
                shading.set(qn("w:color"), "auto")
                shading.set(qn("w:fill"), "0D2240")
                cell._tc.get_or_add_tcPr().append(shading)
                cell.text = _strip_md(rows[0][ci])
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                        run.font.bold = True
                        run.font.size = Pt(10)
            for ri in range(1, len(rows)):
                for ci in range(len(rows[ri])):
                    tbl.cell(ri, ci).text = _strip_md(rows[ri][ci])
                    for paragraph in tbl.cell(ri, ci).paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(10)
                            run.font.color.rgb = RGBColor(0x1A, 0x2D, 0x44)
            doc.add_paragraph("")
        elif kind in ("li", "ol"):
            for it in text.splitlines():
                doc.add_paragraph(_strip_md(it), style="List Bullet" if kind == "li" else "List Number")
        else:
            doc.add_paragraph(_strip_md(text))

    doc.save(str(path))
    return str(path)


# ---------- PDF ----------
def export_pdf(content: str, meta: dict) -> str:
    fname = f"{meta['id']}.pdf"
    path = EXPORT_DIR / fname
    styles = getSampleStyleSheet()

    story = []
    brand = ParagraphStyle(
        "brand",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        textColor=GOLD,
        fontSize=10,
        spaceAfter=6,
    )
    h1 = ParagraphStyle(
        "h1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        textColor=NAVY,
        fontSize=22,
        spaceAfter=14,
    )
    h2 = ParagraphStyle(
        "h2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        textColor=NAVY,
        fontSize=16,
        spaceAfter=10,
    )
    h3 = ParagraphStyle(
        "h3",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        textColor=NAVY,
        fontSize=13,
        spaceAfter=8,
    )
    body = ParagraphStyle(
        "body",
        parent=styles["Normal"],
        fontName="Helvetica",
        textColor=INK,
        fontSize=10.5,
        leading=15,
        spaceAfter=8,
    )
    meta_s = ParagraphStyle(
        "meta", parent=body, textColor=MUTED, fontSize=9, spaceAfter=4
    )

    story.append(Paragraph("4XSTRUCT · STRUCTMIND", brand))
    story.append(Paragraph(meta["mode_label"].upper(), h1))
    story.append(Paragraph(f"Project: {meta.get('project_name', 'Quick Analysis')}", meta_s))
    story.append(Paragraph(f"Generated: {meta.get('completed_at', '')}", meta_s))
    story.append(Paragraph(f"Model: {meta.get('model_used', '')}", meta_s))
    story.append(Paragraph(f"SHA-256: {meta.get('blockchain_hash', '')}", meta_s))
    story.append(Spacer(1, 0.2 * inch))

    for kind, text in _split_blocks(content):
        clean = _strip_md(text)
        if kind == "h1":
            story.append(Paragraph(clean, h1))
        elif kind == "h2":
            story.append(Paragraph(clean, h2))
        elif kind == "h3":
            story.append(Paragraph(clean, h3))
        elif kind == "table":
            rows = _table_rows(text)
            if not rows:
                continue
            header = rows[0]
            body_rows = rows[1:]
            ncol = max(1, len(header))
            # Dense font for wide take-off tables so all columns fit on the page.
            cell = ParagraphStyle(
                "cell", parent=body,
                fontSize=8 if ncol > 8 else 9.5,
                leading=10 if ncol > 8 else 12,
                spaceAfter=0,
            )
            head_cell = ParagraphStyle(
                "headcell", parent=cell,
                fontName="Helvetica-Bold", textColor=colors.white,
            )
            header_cells = [Paragraph(_strip_md(c), head_cell) for c in header]
            tbl_style = TableStyle(
                [
                    ("BACKGROUND",     (0, 0), (-1, 0), NAVY),
                    ("TEXTCOLOR",      (0, 0), (-1, 0), colors.white),
                    ("LEFTPADDING",    (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING",   (0, 0), (-1, -1), 5),
                    ("TOPPADDING",     (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
                    ("GRID",           (0, 0), (-1, -1), 0.5, LINE),
                    ("VALIGN",         (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
                ]
            )
            # Chunk very large tables: each chunk re-prints the header and keeps
            # repeatRows so the build stays fast and the header is always visible.
            CHUNK = 80
            if not body_rows:
                body_rows = [[""] * ncol]
            for start in range(0, len(body_rows), CHUNK):
                chunk = body_rows[start:start + CHUNK]
                data = [list(header_cells)] + [
                    [Paragraph(_strip_md(c), cell) for c in r] for r in chunk
                ]
                tbl = Table(data, repeatRows=1)
                tbl.setStyle(tbl_style)
                story.append(tbl)
            story.append(Spacer(1, 0.15 * inch))
        elif kind in ("li", "ol"):
            for idx, it in enumerate(text.splitlines(), 1):
                bullet = "•" if kind == "li" else f"{idx}."
                story.append(Paragraph(f"{bullet}  {_strip_md(it)}", body))
        else:
            story.append(Paragraph(clean, body))

    pdf = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.75 * inch,
        title=meta.get("project_name") or meta["mode_label"],
        author="4XStruct",
    )
    pdf.build(story)
    return str(path)


EXPORTERS = {
    "markdown": export_markdown,
    "csv": export_csv,
    "xlsx": export_xlsx,
    "docx": export_docx,
    "pdf": export_pdf,
}


def generate_all_exports(content: str, meta: dict) -> list[dict]:
    results = []
    for fmt, fn in EXPORTERS.items():
        try:
            p = fn(content, meta)
            results.append({"format": fmt, "path": p})
        except Exception as e:  # noqa: BLE001
            results.append({"format": fmt, "path": "", "error": str(e)})
    return results
