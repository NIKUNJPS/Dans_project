"""Binder planning — turn an uploaded drawing set into page-level batches.

A real bid binder is several PDFs (structural, architectural, specs, addenda) adding up
to tens of megabytes and hundreds of sheets. Sending that as one request either fails
(Claude's PDF pipeline has per-request page and size limits) or dilutes attention across
too many sheets. Splitting by FILE doesn't help when one PDF alone holds 300 sheets.

This module splits by PAGE:

  1. Every PDF is opened and its pages counted. Documents larger than the batch page
     budget are cut into page-range chunks (pypdf rewrite — which also normalises the
     odd xref/object-stream structure some tools emit). A chunk that is still over the
     size budget is halved until it fits.
  2. Images are re-encoded when they exceed the image limits; text/CSV files ride along.
  3. The pieces are packed, IN DOCUMENT ORDER, into batches bounded by pages and MB, so
     each batch is a contiguous slice of the binder and sheet order is preserved.

Vision tiles (ingest.py) ride along with their page: a PDF with tiled pages is cut into
page ranges whose IMAGE budget (each page + each of its tiles) stays within
VISION_MAX_IMAGES, and each range is followed by the tiles of its pages, so a page and its
high-resolution crops always reach the model in the same call.

The plan also carries a BINDER MANIFEST (every document, its page count, and where each
batch sits) that is given to the model on every call — each batch knows it is part of a
larger set, and the consolidation step knows the whole binder was covered.
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Page budget per model call. Fewer pages per call = sharper reading of each sheet;
# the batches run in parallel, so this mostly trades cost for accuracy, not time.
DEFAULT_BATCH_PAGES = int(os.environ.get("BINDER_BATCH_PAGES", "25"))
# Size budget per model call (MB). Files are sent via the Files API, but very large
# payloads can still be rejected, so keep each call comfortably small.
DEFAULT_BATCH_MB = float(os.environ.get("BINDER_BATCH_MB", "28"))
# Images above either limit are re-encoded (Claude downsamples to ~1.5k px anyway).
_IMAGE_MAX_BYTES = int(4.5 * 1024 * 1024)
_IMAGE_MAX_PX = 7900
_IMAGE_TARGET_PX = 3600
# A text/CSV file counts as one "page" per this many bytes when packing batches, and
# is split (on line boundaries) into parts no larger than _TEXT_PART_BYTES.
_TEXT_BYTES_PER_PAGE = 40_000
_TEXT_PART_BYTES = 200_000
# Vision tiles: images per call (each PDF page counts 1, each tile 1), tiled pages per call,
# and a byte budget for the tile images of one call (they go through the Files API).
DEFAULT_MAX_IMAGES = int(os.environ.get("VISION_MAX_IMAGES", "80"))
DEFAULT_TILED_PAGES = int(os.environ.get("VISION_BATCH_PAGES", "8"))
DEFAULT_TILE_MB = float(os.environ.get("VISION_BATCH_TILE_MB", "150"))

_MB = 1024 * 1024


@dataclass
class Unit:
    """One attachment sent to the model: a whole file or a page-range chunk of one."""
    path: str
    mime: str
    doc_index: int
    doc_name: str
    doc_pages: int
    page_start: int
    page_end: int
    size_bytes: int
    is_temp: bool = False

    @property
    def pages(self) -> int:
        if self.is_tile:
            return 0
        return max(1, self.page_end - self.page_start + 1)

    @property
    def is_pdf(self) -> bool:
        return self.mime == "application/pdf"

    part: int = 0          # text files split into parts: 1-based part number
    parts: int = 0
    caption: str = ""      # vision tile: caption shown to the model next to the image
    is_tile: bool = False  # a rendered crop of a page that is attached in the same batch

    @property
    def images(self) -> int:
        """Images this unit adds to a call (a tile is one image; pages are one each)."""
        return 1 if self.is_tile else self.pages

    @property
    def title(self) -> str:
        """Document title shown to the model for this attachment."""
        if self.caption:
            return self.caption
        if self.parts > 1:
            return f"D{self.doc_index} {self.doc_name} — part {self.part} of {self.parts}"
        if self.is_pdf and self.doc_pages > 1:
            if self.page_start == 1 and self.page_end == self.doc_pages:
                return f"D{self.doc_index} {self.doc_name} — all {self.doc_pages} pages"
            return (f"D{self.doc_index} {self.doc_name} — pages {self.page_start}-{self.page_end} "
                    f"of {self.doc_pages}")
        return f"D{self.doc_index} {self.doc_name}"


@dataclass
class Batch:
    index: int
    units: list[Unit] = field(default_factory=list)

    @property
    def pages(self) -> int:
        return sum(u.pages for u in self.units)

    @property
    def size_bytes(self) -> int:
        return sum(u.size_bytes for u in self.units if not u.is_tile)

    @property
    def images(self) -> int:
        return sum(u.images for u in self.units)

    @property
    def tile_bytes(self) -> int:
        return sum(u.size_bytes for u in self.units if u.is_tile)

    def describe(self) -> str:
        return describe_units(self.units)


def describe_units(units: list[Unit]) -> str:
    """'D1 S.pdf — pages 1-8 of 40 + 36 vision tiles' (tiles are summarised, not listed)."""
    docs = [u.title for u in units if not u.is_tile]
    tiles = sum(1 for u in units if u.is_tile)
    text = "; ".join(docs)
    return f"{text} + {tiles} high-resolution vision tile{'s' if tiles != 1 else ''}" if tiles else text


@dataclass
class BinderPlan:
    documents: list[dict]
    batches: list[Batch]
    skipped: list[str]
    temp_dir: str | None = None

    @property
    def total_pages(self) -> int:
        return sum(d["pages"] for d in self.documents)

    @property
    def total_mb(self) -> float:
        return round(sum(d["size_bytes"] for d in self.documents) / _MB, 1)

    @property
    def is_single_call(self) -> bool:
        return len(self.batches) <= 1

    def manifest_text(self) -> str:
        """Compact listing of the whole binder, given to the model on every call."""
        lines = [
            f"BINDER MANIFEST — {len(self.documents)} document(s), {self.total_pages} page(s), "
            f"{self.total_mb} MB" + (
                f", reviewed in {len(self.batches)} consecutive page batches" if len(self.batches) > 1 else ""
            ) + ":"
        ]
        for d in self.documents:
            unit = "page" if d["pages"] == 1 else "pages"
            lines.append(f"  D{d['index']}  {d['name']} — {d['pages']} {unit}")
        if len(self.batches) > 1:
            lines.append("Batches:")
            for b in self.batches:
                lines.append(f"  Batch {b.index}: {b.describe()}")
        if self.skipped:
            lines.append("Not readable by the engine (export to PDF to include): " + ", ".join(self.skipped))
        return "\n".join(lines)

    def summary(self) -> dict:
        return {
            "documents": [
                {k: d[k] for k in ("index", "name", "pages", "size_mb")} for d in self.documents
            ],
            "total_pages": self.total_pages,
            "total_mb": self.total_mb,
            "batches": len(self.batches),
            "vision_tiles": sum(b.images - b.pages for b in self.batches),
            "skipped": list(self.skipped),
        }

    def cleanup(self) -> None:
        if self.temp_dir:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None


# ─────────────────────────────────────────────────────────────────────────────
# PDF helpers
# ─────────────────────────────────────────────────────────────────────────────

def _open_pdf(path: str):
    from pypdf import PdfReader

    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            return None
    return reader


def count_pdf_pages(path: str) -> int | None:
    try:
        reader = _open_pdf(path)
        return len(reader.pages) if reader is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_page_count_failed path=%s error=%s", path, exc)
        return None


def _write_pdf_range(reader, start: int, end: int, out_path: str) -> int:
    """Write pages [start, end] (1-based, inclusive) to out_path; return bytes written."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for i in range(start - 1, end):
        writer.add_page(reader.pages[i])
    try:
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    except Exception:  # noqa: BLE001 — older pypdf / odd files: an uncompressed chunk is fine
        pass
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return os.path.getsize(out_path)


def _split_pdf(path: str, doc_index: int, doc_name: str, total_pages: int, max_pages: int,
               max_bytes: int, out_dir: str) -> list[Unit] | None:
    """Split one PDF into page-range units that each fit the page and size budget."""
    try:
        reader = _open_pdf(path)
        if reader is None:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_open_failed path=%s error=%s", path, exc)
        return None

    units: list[Unit] = []

    def emit(start: int, end: int) -> None:
        out_path = os.path.join(out_dir, f"d{doc_index:02d}_p{start:04d}-{end:04d}.pdf")
        size = _write_pdf_range(reader, start, end, out_path)
        if size > max_bytes and end > start:
            os.remove(out_path)
            mid = (start + end) // 2
            emit(start, mid)
            emit(mid + 1, end)
            return
        units.append(Unit(out_path, "application/pdf", doc_index, doc_name, total_pages,
                          start, end, size, is_temp=True))

    try:
        n_chunks = math.ceil(total_pages / max_pages)
        # Even chunk sizes (e.g. 60 pages at 25/batch → 20+20+20, not 25+25+10).
        per_chunk = math.ceil(total_pages / n_chunks)
        for start in range(1, total_pages + 1, per_chunk):
            emit(start, min(total_pages, start + per_chunk - 1))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_split_failed path=%s error=%s", path, exc)
        for u in units:
            try:
                os.remove(u.path)
            except OSError:
                pass
        return None
    return units


def _tile_ranges(total_pages: int, doc_tiles: dict[int, list[dict]], max_pages: int, max_images: int,
                 tiled_pages: int, tile_bytes: int) -> list[tuple[int, int]]:
    """Greedy contiguous page ranges whose image / tiled-page / tile-byte budgets fit."""
    ranges: list[tuple[int, int]] = []
    start, pages, images, tiled, nbytes = 1, 0, 0, 0, 0
    for p in range(1, total_pages + 1):
        tl = doc_tiles.get(p) or []
        cost = 1 + len(tl)
        tb = sum(int(t.get("bytes") or 0) for t in tl)
        if pages and (pages + 1 > max_pages or images + cost > max_images
                      or (tl and tiled + 1 > tiled_pages) or (tl and nbytes + tb > tile_bytes)):
            ranges.append((start, p - 1))
            start, pages, images, tiled, nbytes = p, 0, 0, 0, 0
        pages += 1
        images += cost
        tiled += 1 if tl else 0
        nbytes += tb
    ranges.append((start, total_pages))
    return ranges


def _tile_unit(tile: dict, doc_index: int, doc_name: str, doc_pages: int, page: int, k: int, n: int) -> Unit | None:
    path = tile.get("path") or ""
    if not path or not os.path.exists(path):
        return None
    where = tile.get("position") or f"r{tile.get('row', 0) + 1}c{tile.get('col', 0) + 1}"
    ppf = tile.get("px_per_ft") or {}
    scale = "; ".join(f"{k2} = {v:.1f} px/ft" for k2, v in list(ppf.items())[:3] if isinstance(v, (int, float)))
    dpi = tile.get("dpi")
    caption = (f"D{doc_index} {doc_name} — page {page} · tile {k} of {n} ({where})"
               + (f" · {dpi:.0f} dpi" if isinstance(dpi, (int, float)) else "")
               + (f" · {scale}" if scale else ""))
    mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return Unit(path, mime, doc_index, doc_name, doc_pages, page, page, os.path.getsize(path),
                caption=caption, is_tile=True)


def _split_pdf_tiled(path: str, doc_index: int, doc_name: str, total_pages: int, size: int,
                     doc_tiles: dict[int, list[dict]], max_pages: int, max_bytes: int, max_images: int,
                     tiled_pages: int, tile_bytes: int, out_dir: str) -> list[list[Unit]] | None:
    """Page ranges of a PDF that has vision tiles; each group = [range unit, its tiles...]."""
    try:
        reader = _open_pdf(path)
        if reader is None:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_open_failed path=%s error=%s", path, exc)
        return None
    groups: list[list[Unit]] = []

    def tiles_for(a: int, b: int) -> list[Unit]:
        out = []
        for p in range(a, b + 1):
            tl = doc_tiles.get(p) or []
            for k, t in enumerate(tl, 1):
                u = _tile_unit(t, doc_index, doc_name, total_pages, p, k, len(tl))
                if u:
                    out.append(u)
        return out

    def emit(a: int, b: int) -> None:
        if a == 1 and b == total_pages and size <= max_bytes:
            groups.append([Unit(path, "application/pdf", doc_index, doc_name, total_pages, 1, total_pages, size)]
                          + tiles_for(a, b))
            return
        out_path = os.path.join(out_dir, f"d{doc_index:02d}_p{a:04d}-{b:04d}.pdf")
        written = _write_pdf_range(reader, a, b, out_path)
        if written > max_bytes and b > a:
            os.remove(out_path)
            mid = (a + b) // 2
            emit(a, mid)
            emit(mid + 1, b)
            return
        groups.append([Unit(out_path, "application/pdf", doc_index, doc_name, total_pages, a, b, written,
                            is_temp=True)] + tiles_for(a, b))

    try:
        for a, b in _tile_ranges(total_pages, doc_tiles, max_pages, max_images, tiled_pages, tile_bytes):
            emit(a, b)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf_tiled_split_failed path=%s error=%s", path, exc)
        for g in groups:
            for u in g:
                if u.is_temp:
                    try:
                        os.remove(u.path)
                    except OSError:
                        pass
        return None
    return groups


def _fit_image(path: str, mime: str, out_dir: str, doc_index: int) -> tuple[str, str, bool]:
    """Re-encode an oversized image so the API accepts it. Returns (path, mime, is_temp)."""
    try:
        from PIL import Image

        size = os.path.getsize(path)
        with Image.open(path) as im:
            w, h = im.size
            if size <= _IMAGE_MAX_BYTES and max(w, h) <= _IMAGE_MAX_PX:
                return path, mime, False
            scale = min(1.0, _IMAGE_TARGET_PX / max(w, h))
            im = im.convert("RGB")
            if scale < 1.0:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            out = os.path.join(out_dir, f"d{doc_index:02d}_image.jpg")
            im.save(out, "JPEG", quality=88, optimize=True)
            return out, "image/jpeg", True
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_fit_failed path=%s error=%s", path, exc)
        return path, mime, False


def _split_text(path: str, mime: str, doc_index: int, doc_name: str, size: int, out_dir: str) -> list[Unit]:
    """Text / CSV: one unit, or line-boundary parts when the file is large."""
    pages = max(1, math.ceil(size / _TEXT_BYTES_PER_PAGE))
    if size <= _TEXT_PART_BYTES:
        return [Unit(path, mime, doc_index, doc_name, pages, 1, pages, size)]
    parts: list[list[str]] = [[]]
    current = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        header = None
        for line in fh:
            if header is None:
                header = line
            if current + len(line) > _TEXT_PART_BYTES and parts[-1]:
                parts.append([header] if mime == "text/csv" and header else [])
                current = len(header) if parts[-1] else 0
            parts[-1].append(line)
            current += len(line)
    units: list[Unit] = []
    for i, lines in enumerate(parts, 1):
        out = os.path.join(out_dir, f"d{doc_index:02d}_part{i:03d}.txt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        psize = os.path.getsize(out)
        ppages = max(1, math.ceil(psize / _TEXT_BYTES_PER_PAGE))
        units.append(Unit(out, mime, doc_index, doc_name, pages, 1, ppages, psize,
                          is_temp=True, part=i, parts=len(parts)))
    return units


# ─────────────────────────────────────────────────────────────────────────────
# Planning
# ─────────────────────────────────────────────────────────────────────────────

def plan_binder(
    files: list[tuple[str, str, str]],
    *,
    max_pages: int | None = None,
    max_mb: float | None = None,
    skipped: list[str] | None = None,
    scratch_root: str | None = None,
    tiles: dict[tuple[int, int], list[dict]] | None = None,
    max_images: int | None = None,
) -> BinderPlan:
    """Plan the batches for a set of prepared files.

    files: (path, mime, display_name) in the order the user uploaded them.
    tiles: optional {(doc_index, page): [tile dicts from ingest.render_tiles]} — attached
    right after the page range that holds their page, in the same batch.
    """
    max_pages = max(1, int(max_pages or DEFAULT_BATCH_PAGES))
    max_bytes = int((max_mb or DEFAULT_BATCH_MB) * _MB)
    max_images = max(2, int(max_images or DEFAULT_MAX_IMAGES))
    tile_budget = int(DEFAULT_TILE_MB * _MB)
    temp_dir = tempfile.mkdtemp(prefix="binder_", dir=scratch_root)

    documents: list[dict] = []
    groups: list[list[Unit]] = []        # packing groups: a page range and its tiles stay together
    for idx, (path, mime, name) in enumerate(files, 1):
        if not os.path.exists(path):
            logger.warning("binder_missing_file path=%s", path)
            continue
        size = os.path.getsize(path)
        mime = (mime or "").lower()
        name = name or os.path.basename(path)
        pages = 1
        doc_units: list[Unit] | None = None

        doc_groups: list[list[Unit]] | None = None
        if mime == "application/pdf":
            counted = count_pdf_pages(path)
            pages = counted or 1
            doc_tiles = {p: tl for (d, p), tl in (tiles or {}).items() if d == idx and tl}
            if counted and doc_tiles:
                doc_groups = _split_pdf_tiled(path, idx, name, counted, size, doc_tiles, max_pages, max_bytes,
                                              max_images, DEFAULT_TILED_PAGES, tile_budget, temp_dir)
            if not doc_groups and counted and (counted > max_pages or size > max_bytes):
                doc_units = _split_pdf(path, idx, name, counted, max_pages, max_bytes, temp_dir)
            if not doc_groups and not doc_units:
                doc_units = [Unit(path, mime, idx, name, pages, 1, pages, size)]
        elif mime.startswith("image/"):
            fitted, fmime, is_temp = _fit_image(path, mime, temp_dir, idx)
            doc_units = [Unit(fitted, fmime, idx, name, 1, 1, 1, os.path.getsize(fitted), is_temp)]
        else:
            pages = max(1, math.ceil(size / _TEXT_BYTES_PER_PAGE))
            doc_units = _split_text(path, mime or "text/plain", idx, name, size, temp_dir)

        documents.append({
            "index": idx, "name": name, "mime": mime, "pages": pages,
            "size_bytes": size, "size_mb": round(size / _MB, 2),
        })
        groups.extend(doc_groups or [[u] for u in doc_units or []])

    # Pack groups into batches in binder order.
    batches: list[Batch] = []
    current = Batch(index=1)
    for g in groups:
        g_pages = sum(u.pages for u in g)
        g_bytes = sum(u.size_bytes for u in g if not u.is_tile)
        g_images = sum(u.images for u in g)
        g_tile_bytes = sum(u.size_bytes for u in g if u.is_tile)
        if current.units and (current.pages + g_pages > max_pages or current.size_bytes + g_bytes > max_bytes
                              or ((g_images > g_pages or current.images > current.pages)
                                  and current.images + g_images > max_images)
                              or current.tile_bytes + g_tile_bytes > tile_budget):
            batches.append(current)
            current = Batch(index=len(batches) + 1)
        current.units.extend(g)
    if current.units:
        batches.append(current)

    plan = BinderPlan(documents=documents, batches=batches, skipped=list(skipped or []), temp_dir=temp_dir)
    logger.info(
        "binder_planned documents=%d pages=%d mb=%.1f batches=%d tiles=%d",
        len(documents), plan.total_pages, plan.total_mb, len(batches), sum(b.images - b.pages for b in batches),
    )
    return plan
