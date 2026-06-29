"""Role-aware PDF generator for estimation reports — Detailer + Fabricator only."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

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

# ─── BRAND ─────────────────────────────────────────────
NAVY    = colors.HexColor("#0d2240")
NAVYM   = colors.HexColor("#1a3a5c")
GOLD    = colors.HexColor("#f5a800")
GOLDL   = colors.HexColor("#ffd166")
GOLDP   = colors.HexColor("#fff8e6")
INK     = colors.HexColor("#1a2d44")
MUTED   = colors.HexColor("#6b8299")
LINE    = colors.HexColor("#e2eaf2")
BG      = colors.HexColor("#f7f9fc")
WHITE   = colors.white
SUCCESS = colors.HexColor("#16a34a")
WARN    = colors.HexColor("#f59e0b")
DANGER  = colors.HexColor("#ef4444")

EXPORT_DIR = Path(settings.upload_dir) / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _styles():
    s = getSampleStyleSheet()
    return {
        "brand":    ParagraphStyle("brand",    parent=s["Normal"],   fontName="Helvetica-Bold", textColor=GOLD, fontSize=11, leading=14, spaceAfter=8, letterSpacing=2),
        "title":    ParagraphStyle("title",    parent=s["Heading1"], fontName="Helvetica-Bold", textColor=NAVY, fontSize=26, leading=30, spaceAfter=16),
        "subtitle": ParagraphStyle("subtitle", parent=s["Normal"],   fontName="Helvetica",      textColor=MUTED, fontSize=11, leading=14, spaceAfter=18),
        "h2":       ParagraphStyle("h2",       parent=s["Heading2"], fontName="Helvetica-Bold", textColor=NAVY, fontSize=15, leading=18, spaceBefore=16, spaceAfter=10),
        "body":     ParagraphStyle("body",     parent=s["Normal"],   fontName="Helvetica",      textColor=INK,  fontSize=10.5, leading=15, spaceAfter=6),
        "meta":     ParagraphStyle("meta",     parent=s["Normal"],   fontName="Helvetica",      textColor=MUTED, fontSize=9, leading=12, spaceAfter=3),
        "moneyBig": ParagraphStyle("moneyBig", parent=s["Normal"],   fontName="Helvetica-Bold", textColor=GOLD, fontSize=28, leading=32),
        "tableCell":     ParagraphStyle("tableCell",     parent=s["Normal"], fontName="Helvetica",      textColor=INK,   fontSize=9.5, leading=12),
        "tableCellHead": ParagraphStyle("tableCellHead", parent=s["Normal"], fontName="Helvetica-Bold", textColor=WHITE, fontSize=9.5, leading=12),
    }


def _table(rows, st, col_widths=None, highlight_last=False):
    data = []
    for ri, row in enumerate(rows):
        rendered = []
        style_choice = "tableCellHead" if ri == 0 else "tableCell"
        for cell in row:
            rendered.append(Paragraph(str(cell), st[style_choice]))
        data.append(rendered)
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND",     (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",      (0, 0), (-1, 0), WHITE),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME",       (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOTTOMPADDING",  (0, 0), (-1, 0), 10),
        ("TOPPADDING",     (0, 0), (-1, 0), 10),
        ("LEFTPADDING",    (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 10),
        ("TOPPADDING",     (0, 1), (-1, -1), 8),
        ("BOTTOMPADDING",  (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG]),
        ("LINEBELOW",      (0, 0), (-1, 0), 0, NAVY),
        ("LINEBELOW",      (0, 1), (-1, -1), 0.4, LINE),
        ("LINEAFTER",      (0, 0), (-2, -1), 0.4, LINE),
        ("BOX",            (0, 0), (-1, -1), 0.6, LINE),
    ]
    if highlight_last:
        style_cmds += [
            ("BACKGROUND", (0, -1), (-1, -1), GOLDP),
            ("FONTNAME",   (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE",  (0, -1), (-1, -1), 1.5, GOLD),
        ]
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _header_block(st, role: str, country: str) -> list:
    band = Table(
        [[
            Paragraph("4XSTRUCT · STRUCTMIND", st["brand"]),
            Paragraph(
                f"{role.upper()} · {country.upper()} · {datetime.now().strftime('%d %b %Y')}",
                ParagraphStyle("rhdr", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=WHITE, alignment=2),
            ),
        ]],
        colWidths=[3.6 * inch, 3.6 * inch],
    )
    band.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 14),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
        ("TOPPADDING",    (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return [band, Spacer(1, 0.18 * inch)]


def _kpi_strip(st, kpis):
    cells = []
    for label, value in kpis:
        inner = Table(
            [[Paragraph(label, ParagraphStyle("kpiLabel", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED, letterSpacing=1))],
             [Paragraph(value, ParagraphStyle("kpiVal", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=NAVY))]],
            colWidths=[1.9 * inch],
        )
        inner.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (-1, -1), WHITE),
            ("LINEABOVE", (0, 0), (-1, 0), 2.5, GOLD),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ]))
        cells.append(inner)
    table = Table([cells], colWidths=[2.0 * inch] * len(cells))
    table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return table


# ─── DETAILER ─────────────────────────────────────────
def _render_detailer(result: dict, project: str, st: dict) -> list:
    v = result["visible"]
    is_ai = "extracted" in v
    story = _header_block(st, "DETAILER", result["country"])
    story += [
        Paragraph("DETAILING ESTIMATE", st["title"]),
        Paragraph(f"Project: {project} · Country: {result['country']} · Currency: {result['currency']}", st["subtitle"]),
    ]

    if is_ai:
        ex = v["extracted"]
        story += [
            _kpi_strip(st, [
                ("DETAILING HOURS", f"{ex.get('total_hours', v.get('total_hours', 0)):,.0f}"),
                ("RATE BAND",       f"{v['user_rate_low']}  →  {v['user_rate_high']} / hr"),
                ("FINAL (MID)",     v["grand_mid"]),
            ]),
            Spacer(1, 0.25 * inch),

            Paragraph("WORKLOAD SUMMARY", st["h2"]),
            _table(
                [["Metric", "Value"]] +
                [["Estimated detailing hours",    f"{ex.get('total_hours', 0):,.1f} hrs"],
                 ["Production drawings",          f"{ex.get('drawings', 0):,}"],
                 ["Connections",                  f"{ex.get('connections', 0):,}"],
                 ["Complexity",                   ex.get("complexity", "Medium")],
                 ["Drawings reviewed",            f"{ex.get('drawings_seen', 0):,}"]],
                st, col_widths=[3.6 * inch, 3.6 * inch],
            ),
            Spacer(1, 0.1 * inch),
            Paragraph(f"Note: {ex.get('notes','—')}", st["meta"]),
            Spacer(1, 0.2 * inch),

            Paragraph("RATE BASIS", st["h2"]),
            _table(
                [["Input", "Low", "High"]] +
                [["Detailer rate per hour", v["user_rate_low"], v["user_rate_high"]]],
                st, col_widths=[3.8 * inch, 1.4 * inch, 1.4 * inch],
            ),
            Spacer(1, 0.2 * inch),

            Paragraph("ESTIMATE RANGE (LOW · MID · HIGH)", st["h2"]),
            _table(
                [["Line", "Low", "Mid", "High"]] +
                [["GRAND TOTAL", v["grand_low"], v["grand_mid"], v["grand_high"]]],
                st, col_widths=[2.6 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch], highlight_last=True,
            ),
            Spacer(1, 0.25 * inch),

            Paragraph("FINAL DETAILING RANGE", st["h2"]),
            Paragraph(v["grand_range_text"], st["moneyBig"]),
            Paragraph(
                f"Mid-point headline: {v['final_amount']} · Detailing hours × hourly rate band.",
                st["meta"],
            ),
        ]
        return story

    # Deterministic (legacy /calculate path)
    story += [
        _kpi_strip(st, [
            ("TOTAL HOURS", f"{v['total_hours']:,.0f}"),
            ("TIMELINE",    f"{v['timeline_weeks']:.0f} weeks"),
            ("FINAL COST",  v["final_amount"]),
        ]),
        Spacer(1, 0.25 * inch),

        Paragraph("SCOPE OF WORK", st["h2"]),
        Paragraph(v["scope_summary"], st["body"]),
        Spacer(1, 0.08 * inch),

        Paragraph("HOURS BREAKDOWN BY CATEGORY", st["h2"]),
        _table(
            [["Category", "Quantity", "Unit", "Hours"]] +
            [[b["item"], str(b.get("qty", "-")), b.get("unit", ""), f"{b['hours']:,.1f}"] for b in result["breakdown"]] +
            [["TOTAL HOURS", "", "", f"{v['total_hours']:,.1f}"]],
            st, col_widths=[3.0 * inch, 1.0 * inch, 1.0 * inch, 1.3 * inch], highlight_last=True,
        ),
        Spacer(1, 0.2 * inch),

        Paragraph("DELIVERABLES INCLUDED", st["h2"]),
    ]
    for d in v["deliverables"]:
        story.append(Paragraph(f"&#9656; &nbsp; {d}", st["body"]))
    story += [
        Spacer(1, 0.25 * inch),
        Paragraph("FINAL DETAILING COST", st["h2"]),
        Paragraph(v["final_amount"], st["moneyBig"]),
        Paragraph(f"All-inclusive · {result['currency']} · Excludes taxes (where applicable)", st["meta"]),
    ]
    return story


# ─── FABRICATOR (range-based) ─────────────────────────
def _render_fabricator(result: dict, project: str, st: dict) -> list:
    v = result["visible"]
    is_ai = "extracted" in v
    story = _header_block(st, "FABRICATOR", result["country"])
    story += [
        Paragraph("FABRICATION ESTIMATE", st["title"]),
        Paragraph(f"Project: {project} · Country: {result['country']} · Currency: {result['currency']}", st["subtitle"]),
    ]

    if is_ai:
        ex = v["extracted"]
        story += [
            _kpi_strip(st, [
                ("TONNAGE",     f"{ex['tonnage']:,.1f} t"),
                ("RATE BAND",   f"{v['user_rate_low']}  →  {v['user_rate_high']} / ton"),
                ("FINAL (MID)", v["grand_mid"]),
            ]),
            Spacer(1, 0.25 * inch),

            Paragraph("TONNAGE TAKE-OFF", st["h2"]),
            _table(
                [["Metric", "Value"]] +
                [["Total fabricated tonnage", f"{ex['tonnage']:,.2f} t"],
                 ["Distinct members counted", f"{ex['members_counted']:,}"],
                 ["Primary material",         ex.get("primary_material", "—")],
                 ["Drawings reviewed",        f"{ex['drawings_seen']:,}"]],
                st, col_widths=[3.6 * inch, 3.6 * inch],
            ),
            Spacer(1, 0.1 * inch),
            Paragraph(f"Note: {ex.get('notes','—')}", st["meta"]),
            Spacer(1, 0.2 * inch),

            Paragraph("USER-PROVIDED RATE BAND", st["h2"]),
            _table(
                [["Input", "Low", "High"]] +
                [["Per-ton cost (user)", v["user_rate_low"], v["user_rate_high"]]],
                st, col_widths=[3.8 * inch, 1.4 * inch, 1.4 * inch],
            ),
            Spacer(1, 0.2 * inch),

            Paragraph("ESTIMATE RANGE (LOW · MID · HIGH)", st["h2"]),
            _table(
                [["Line", "Low", "Mid", "High"]] +
                [["Subtotal", v["subtotal_low"], v["subtotal_mid"], v["subtotal_high"]],
                 [v["tax_label"], v["tax_low"], v["tax_mid"], v["tax_high"]],
                 ["GRAND TOTAL", v["grand_low"], v["grand_mid"], v["grand_high"]]],
                st, col_widths=[2.6 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch], highlight_last=True,
            ),
            Spacer(1, 0.25 * inch),

            Paragraph("FINAL FABRICATION RANGE", st["h2"]),
            Paragraph(v["grand_range_text"], st["moneyBig"]),
            Paragraph(
                f"Mid-point headline: {v['final_amount']} · tonnage × your per-ton band · includes {result['country']} tax.",
                st["meta"],
            ),
        ]
        return story

    # Deterministic (legacy /calculate path)
    story += [
        _kpi_strip(st, [
            ("TONNAGE",      f"{v['tonnage']:,.1f} t"),
            ("RATE BAND",    f"{v['adjusted_rate_low']}  →  {v['adjusted_rate_high']} / ton"),
            ("FINAL (MID)",  v["grand_mid"]),
        ]),
        Spacer(1, 0.25 * inch),

        Paragraph("USER-PROVIDED COST BAND (PRE-ADJUSTMENT)", st["h2"]),
        _table(
            [["Input", "Low", "High"]] +
            [["Per-ton cost provided", v["user_rate_low"], v["user_rate_high"]],
             [f"Composite factor (material × surface × assembly = ×{v['composite_factor']})",
              v["adjusted_rate_low"], v["adjusted_rate_high"]]],
            st, col_widths=[3.8 * inch, 1.4 * inch, 1.4 * inch],
        ),
        Spacer(1, 0.2 * inch),

        Paragraph("ESTIMATE RANGE (LOW · MID · HIGH)", st["h2"]),
        _table(
            [["Line", "Low", "Mid", "High"]] +
            [["Subtotal", v["subtotal_low"], v["subtotal_mid"], v["subtotal_high"]],
             [v["tax_label"], v["tax_low"], v["tax_mid"], v["tax_high"]],
             ["GRAND TOTAL", v["grand_low"], v["grand_mid"], v["grand_high"]]],
            st, col_widths=[2.6 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch], highlight_last=True,
        ),
        Spacer(1, 0.25 * inch),

        Paragraph("PROCESS COST BREAKDOWN (MID SCENARIO)", st["h2"]),
        _table(
            [["Process Activity", "Share", "Amount"]] +
            [[r["process"], r["share"], r["amount"]] for r in v["process_breakdown"]] +
            [["MID SUBTOTAL", "", v["subtotal_mid"]]],
            st, col_widths=[3.6 * inch, 1.0 * inch, 1.7 * inch], highlight_last=True,
        ),
        Spacer(1, 0.25 * inch),

        Paragraph("MATERIAL & SHOP ACTIVITIES", st["h2"]),
        _table(
            [["Item", "Quantity / Spec", "Adjustment Factor"]] +
            [[b["item"], f"{b.get('qty','')} {b.get('unit','')}".strip(), b.get("rate", "")]
             for b in result["breakdown"]],
            st, col_widths=[2.5 * inch, 2.4 * inch, 1.4 * inch],
        ),
        Spacer(1, 0.25 * inch),

        Paragraph("FINAL FABRICATION RANGE", st["h2"]),
        Paragraph(v["grand_range_text"], st["moneyBig"]),
        Paragraph(
            f"Mid-point headline figure: {v['grand_mid']} · Includes tax ({result['country']}) · "
            f"Range driven by user-provided per-ton band.",
            st["meta"],
        ),
    ]
    return story


RENDERERS = {
    "detailer":   _render_detailer,
    "fabricator": _render_fabricator,
}


def render_pdf(result: dict, project: str, *, filename: str | None = None) -> str:
    st = _styles()
    renderer = RENDERERS.get(result["role"])
    if not renderer:
        raise ValueError(f"No PDF renderer for role '{result['role']}'. Allowed: detailer, fabricator.")
    story = renderer(result, project, st)

    fname = filename or f"estimation_{result['role']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    path = EXPORT_DIR / fname
    pdf = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title=f"STRUCTMIND · {result['role'].title()} Estimate",
        author="4XStruct Inc.",
    )
    pdf.build(story)
    return str(path)
