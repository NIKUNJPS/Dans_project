"""Turn uploaded files into something the model can actually read.

LLMs (Claude, Gemini, all of them) can only ingest PDF, images, and text. This module
widens real-world format support by converting common engineering / office formats to
text or CSV *server-side* before they are sent to the model:

  • Pass-through (sent as-is) ...... PDF, PNG, JPG/JPEG, WEBP, TXT, CSV
  • Excel  → CSV .................... XLSX, XLSM
  • Word   → text .................. DOCX
  • Text-based engineering files ... NC1, NC, DSTV, MIS, SDNF, KSS, XML, JSON, STP/STEP
                                     (read as plain text)

Proprietary *binary* CAD/BIM (DWG, DXF-binary, RVT, NWD, IFC) and legacy Office binaries
(DOC, XLS) cannot be decoded in-process — there is no pure-Python reader. Those return a
"needs_export" status so the caller can tell the user to export them to PDF.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Sent to the model unchanged. Canonical MIME per extension.
_PASSTHROUGH_EXT = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_PASSTHROUGH_MIMES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "image/png",
    "image/jpeg",
    "image/webp",
}
# ASCII / text-based formats read verbatim as plain text.
_TEXT_LIKE_EXT = {
    ".nc1", ".nc", ".dstv", ".mis", ".sdnf", ".kss",
    ".xml", ".json", ".stp", ".step", ".md", ".log",
}
_EXCEL_EXT = {".xlsx", ".xlsm"}
_WORD_EXT = {".docx"}
# Binary formats that need an external converter — export to PDF instead.
_NEEDS_EXPORT_EXT = {".dwg", ".dxf", ".rvt", ".nwd", ".ifc", ".doc", ".xls"}

# Guard against a single monster file blowing the context / memory.
_TEXT_CAP_BYTES = 6_000_000


def _excel_to_csv(src: Path, dst: Path) -> None:
    """Flatten every worksheet of an .xlsx/.xlsm into one CSV, sheet-labelled."""
    from openpyxl import load_workbook

    wb = load_workbook(src, read_only=True, data_only=True)
    try:
        with open(dst, "w", newline="", encoding="utf-8") as out:
            w = csv.writer(out)
            for ws in wb.worksheets:
                w.writerow([f"# SHEET: {ws.title}"])
                for row in ws.iter_rows(values_only=True):
                    w.writerow(["" if c is None else c for c in row])
                w.writerow([])
    finally:
        wb.close()


def _docx_to_text(src: Path, dst: Path) -> None:
    """Extract paragraphs and tables from a .docx as plain text."""
    import docx

    d = docx.Document(str(src))
    lines: list[str] = []
    for p in d.paragraphs:
        if p.text.strip():
            lines.append(p.text)
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    dst.write_text("\n".join(lines), encoding="utf-8")


def _raw_text(src: Path, dst: Path) -> None:
    """Decode a text-based file (utf-8, latin-1 fallback), capped."""
    data = src.read_bytes()[:_TEXT_CAP_BYTES]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    dst.write_text(text, encoding="utf-8")


def prepare_file(
    f: dict, upload_dir: Path, scratch: Path
) -> tuple[tuple[str, str] | None, str]:
    """Resolve one uploaded file to a model-readable (path, mime), converting if needed.

    Returns ``(pair, status)`` where ``pair`` is ``(path, mime)`` or ``None``.
    status ∈ {"ok", "missing", "needs_export", "convert_failed", "unsupported"}.
    """
    src = Path(upload_dir) / f.get("storage_key", "")
    if not src.exists():
        return None, "missing"

    name = f.get("original_name") or f.get("storage_key") or ""
    ext = Path(name).suffix.lower()
    mt = (f.get("mime_type") or "").split(";")[0].strip().lower()

    # 1) Already readable — send as-is.
    if mt in _PASSTHROUGH_MIMES:
        return (str(src), mt), "ok"
    if ext in _PASSTHROUGH_EXT:
        return (str(src), _PASSTHROUGH_EXT[ext]), "ok"

    # 2) Convert what we can.
    stem = src.stem or "file"
    try:
        if ext in _EXCEL_EXT:
            dst = scratch / f"{stem}.csv"
            _excel_to_csv(src, dst)
            return (str(dst), "text/csv"), "ok"
        if ext in _WORD_EXT:
            dst = scratch / f"{stem}.txt"
            _docx_to_text(src, dst)
            return (str(dst), "text/plain"), "ok"
        if ext in _TEXT_LIKE_EXT:
            dst = scratch / f"{stem}{ext}.txt"
            _raw_text(src, dst)
            return (str(dst), "text/plain"), "ok"
    except Exception as e:  # noqa: BLE001 — a bad file must not kill the analysis
        logger.warning("file_prep convert failed name=%s ext=%s err=%s", name, ext, e)
        return None, "convert_failed"

    # 3) Proprietary binary — needs an external converter.
    if ext in _NEEDS_EXPORT_EXT:
        return None, "needs_export"
    return None, "unsupported"
