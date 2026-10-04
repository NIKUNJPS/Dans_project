"""Last-resort PDF repair tiers, used only when Claude rejects a specific attachment.

  1. sanitize_pdf   — pypdf structural rewrite (fixes non-standard xref tables / object
                      streams that strict parsers reject; tools like PDFsam emit these).
  2. rasterize_pdf  — render every page to JPEG and rebuild a fresh PDF (fixes bad
                      embedded image encodings such as JBIG2/CCITT scans). Runs in a
                      disposable SUBPROCESS: MuPDF has hard-crashed the whole server on a
                      corrupted file before, and a dead worker process is recoverable.

Memory is bounded per page during rasterization: a large-format sheet (ARCH D/E) at a
flat DPI can exceed 100 megapixels, so every page is capped on its longest side.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

logger = logging.getLogger(__name__)

# spawn, not fork: the server runs an asyncio loop with worker threads, and forking a
# multi-threaded process can leave locks held forever in the child.
_MP_SPAWN_CONTEXT = multiprocessing.get_context("spawn")


def sanitize_pdf(file_path: str) -> str | None:
    """Re-serialize a PDF with pypdf. Returns the repaired copy's path, or None."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        logger.warning("pypdf_not_installed — cannot attempt PDF repair")
        return None
    try:
        reader = PdfReader(file_path, strict=False)
        if reader.is_encrypted:
            reader.decrypt("")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        fd, out_path = tempfile.mkstemp(suffix=".repaired.pdf")
        os.close(fd)
        with open(out_path, "wb") as fh:
            writer.write(fh)
        return out_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_repair_failed path=%s error=%s", file_path, exc)
        return None


def rasterize_pdf(file_path: str, dpi: int = 150, max_dimension_px: int = 2400) -> str | None:
    """Render every page to an image and rebuild a brand-new PDF from those images."""
    try:
        import pymupdf as fitz
    except ImportError:
        logger.warning("pymupdf_not_installed — cannot attempt PDF rasterization")
        return None

    img_paths: list[str] = []
    src = out = None
    try:
        src = fitz.open(file_path)
        out = fitz.open()
        rendered = 0
        for page_num in range(len(src)):
            pix = None
            try:
                page = src[page_num]
                rect = page.rect
                zoom = dpi / 72.0
                if max(rect.width, rect.height) * zoom > max_dimension_px:
                    zoom = max_dimension_px / max(rect.width, rect.height)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                fd, img_path = tempfile.mkstemp(suffix=".jpg")
                os.close(fd)
                pix.save(img_path, jpg_quality=85)
                img_paths.append(img_path)
                new_page = out.new_page(width=pix.width, height=pix.height)
                new_page.insert_image(fitz.Rect(0, 0, pix.width, pix.height), filename=img_path)
                rendered += 1
            except Exception as page_exc:  # noqa: BLE001
                logger.warning("pdf_rasterize_page_failed path=%s page=%d error=%s",
                               file_path, page_num, page_exc)
            finally:
                pix = None
        if rendered == 0:
            return None
        fd, out_path = tempfile.mkstemp(suffix=".rasterized.pdf")
        os.close(fd)
        out.save(out_path)
        return out_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_rasterize_failed path=%s error=%s", file_path, exc)
        return None
    finally:
        if out is not None:
            out.close()
        if src is not None:
            src.close()
        for p in img_paths:
            try:
                os.remove(p)
            except OSError:
                pass


def rasterize_pdf_isolated(file_path: str) -> str | None:
    """rasterize_pdf in a throwaway subprocess, so a MuPDF crash can't take the server down."""
    try:
        with ProcessPoolExecutor(max_workers=1, mp_context=_MP_SPAWN_CONTEXT) as executor:
            return executor.submit(rasterize_pdf, file_path).result(timeout=300)
    except BrokenProcessPool as exc:
        logger.warning("pdf_rasterize_subprocess_crashed path=%s error=%s", file_path, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_rasterize_subprocess_failed path=%s error=%s", file_path, exc)
        return None


REPAIR_TIERS = (
    (sanitize_pdf, "repaired"),
    (rasterize_pdf_isolated, "rasterized"),
)
