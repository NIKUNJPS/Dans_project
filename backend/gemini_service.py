"""LLM service — Anthropic Claude (Messages API + Files API).

Converted from Google Gemini to the Anthropic Claude API. The PUBLIC INTERFACE is
unchanged (`run_analysis`, `MODEL_CHAIN`, `engine_label`, `_get_client`) so file
batching, the deterministic merge, exports, and the estimation/tonnage paths keep
working — only the model backend is swapped.

WHY CLAUDE (Opus 4.8):
  • Stronger, more literal structural-drawing reading and take-off accuracy.
  • 1M input context + 128K max output → far fewer continuation stitches on big tables.
  • Files API (up to 500 MB/file) handles the large drawing PDFs the base64 path can't.

KEY BEHAVIOURS:
  1. Files are uploaded to the Anthropic Files API and referenced by file_id; kept
     alive across every continuation of a batch, deleted only after the batch finishes.
  2. Adaptive thinking + effort=high for maximum extraction accuracy.
  3. Prompt caching on the uploaded-file prefix so continuations don't re-bill the PDFs.
  4. Continuation on stop_reason == "max_tokens": the model's own (thinking + text)
     turn is echoed back verbatim and it is asked to continue — this ends on a USER
     turn, so it is NOT an assistant prefill (which Opus 4.8 rejects).
  5. Streaming is used for the large max_tokens so requests never hit HTTP timeouts.
  6. Model fallback: Opus 4.8 → Sonnet 5 on any error or refusal.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

import anthropic

from config import settings
from report_merge import merge_reports, normalize_section_headings, strip_summary_footer

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Model chain & labels
# ─────────────────────────────────────────────────────────────────────────────

MODEL_CHAIN: list[str] = [
    "claude-sonnet-5",   # primary — near-Opus accuracy for MTO at ~40-50% lower cost
    "claude-opus-4-8",   # fallback — most capable, used only if the primary errors
]

# Client-facing labels. Sonnet is the production engine, so it carries the "PRO"
# badge; Opus (the pricier fallback) shows "ULTRA". No report ever downgrades to "FAST".
ENGINE_LABELS: dict[str, str] = {
    "claude-sonnet-5": "STRUCTMIND CORE · PRO",
    "claude-opus-4-8": "STRUCTMIND CORE · ULTRA",
    "claude-haiku-4-5": "STRUCTMIND CORE · LITE",
}

# ─────────────────────────────────────────────────────────────────────────────
# Tunable limits
# ─────────────────────────────────────────────────────────────────────────────

MAX_BATCH_MB        = 90.0   # max total MB per upload batch (Files API allows 500MB/file)
MAX_FILES_PER_BATCH = 8      # files per batch (product spec: 8 per batch)

# Output token budget per call. Opus 4.8 / Sonnet 5 support up to 128K output; we use
# 64K (streamed) — large enough that most take-offs finish in one or two calls, and
# continuation stitches the rest. Adaptive thinking shares this budget with the answer.
MAX_OUTPUT_TOKENS   = 64_000

# Continuation cap — a single batch can stream an effectively unlimited take-off by
# stitching ~64K-token chunks until the model stops. This is a safety cap, not a length.
MAX_CONTINUATIONS   = 24

# Beta header required to reference uploaded files by file_id.
FILES_BETA = "files-api-2025-04-14"

# Thread pool for the blocking Anthropic calls (upload, streaming get_final_message).
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def engine_label(internal_model: str) -> str:
    return ENGINE_LABELS.get(internal_model, "STRUCTMIND CORE")


def _get_client() -> anthropic.Anthropic:
    """Return an authenticated Anthropic client.

    Uses ANTHROPIC_API_KEY (from settings/env). The zero-arg constructor also reads the
    env var, so this works whether or not the key is surfaced through settings.
    """
    if settings.anthropic_api_key:
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()
    raise RuntimeError("ANTHROPIC_API_KEY is not configured")


def _text_of(message) -> str:
    """Concatenate the visible text blocks of a Claude response (skips thinking blocks)."""
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _ends_mid_table_row(text: str) -> bool:
    """True if `text` ends in the MIDDLE of a markdown table row.

    A complete row ends with '|'. If the last non-empty line opens a row ('|...') but
    has no closing '|', the model was cut off mid-row and the continuation must be glued
    on with no separator so the row is not split into two broken rows.
    """
    for line in reversed(text.splitlines()):
        if not line.strip():
            continue
        ls = line.strip()
        return ls.startswith("|") and not ls.endswith("|")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# File batching
# ─────────────────────────────────────────────────────────────────────────────

def _build_batches(
    file_paths: list[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    """Split file_paths into batches under MAX_BATCH_MB and MAX_FILES_PER_BATCH.
    Largest files first.
    """
    sorted_files = sorted(
        file_paths,
        key=lambda x: os.path.getsize(x[0]) if os.path.exists(x[0]) else 0,
        reverse=True,
    )

    batches: list[list[tuple[str, str]]] = []
    batch_sizes: list[float] = []

    for fp, mime in sorted_files:
        if not os.path.exists(fp):
            logger.warning("file_not_found path=%s", fp)
            continue
        size_mb = os.path.getsize(fp) / (1_024 * 1_024)

        placed = False
        for i, batch in enumerate(batches):
            if (
                len(batch) < MAX_FILES_PER_BATCH
                and batch_sizes[i] + size_mb <= MAX_BATCH_MB
            ):
                batch.append((fp, mime))
                batch_sizes[i] += size_mb
                placed = True
                break

        if not placed:
            batches.append([(fp, mime)])
            batch_sizes.append(size_mb)

    for i, (batch, sz) in enumerate(zip(batches, batch_sizes)):
        logger.info(
            "file_batch batch=%d/%d files=%d size_mb=%.1f",
            i + 1, len(batches), len(batch), sz,
        )
    return batches


# ─────────────────────────────────────────────────────────────────────────────
# File upload (Anthropic Files API) — blocking, wrapped for async
# ─────────────────────────────────────────────────────────────────────────────

def _upload_files_sync(
    client: anthropic.Anthropic,
    file_paths: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Upload files to the Anthropic Files API (blocking).

    Returns a list of (file_id, mime_type). Called via run_in_executor.
    """
    uploaded: list[tuple[str, str]] = []
    for file_path, mime_type in file_paths:
        if not os.path.exists(file_path):
            logger.warning("upload_skip_missing path=%s", file_path)
            continue
        size_mb = os.path.getsize(file_path) / (1_024 * 1_024)
        logger.info("uploading path=%s size_mb=%.1f", file_path, size_mb)
        try:
            with open(file_path, "rb") as fh:
                info = client.beta.files.upload(
                    file=(os.path.basename(file_path), fh, mime_type),
                )
            uploaded.append((info.id, mime_type))
            logger.info("upload_ready id=%s size_mb=%.1f", info.id, size_mb)
        except Exception as exc:  # noqa: BLE001
            logger.warning("upload_error path=%s error=%s", file_path, exc)
    return uploaded


async def _upload_files(
    client: anthropic.Anthropic,
    file_paths: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _EXECUTOR, _upload_files_sync, client, file_paths
    )


def _cleanup_files(client: anthropic.Anthropic, uploaded: list[tuple[str, str]]) -> None:
    for file_id, _mime in uploaded:
        try:
            client.beta.files.delete(file_id)
            logger.info("file_deleted id=%s", file_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("file_delete_error id=%s error=%s", file_id, exc)


def _file_block(file_id: str, mime: str) -> dict:
    """Build the content block that references an uploaded file by id."""
    if (mime or "").startswith("image/"):
        return {"type": "image", "source": {"type": "file", "file_id": file_id}}
    # PDFs, text, csv → document blocks
    return {"type": "document", "source": {"type": "file", "file_id": file_id}}


def _sanitize_pdf(file_path: str) -> str | None:
    """Re-serialize a PDF with pypdf, normalizing its internal structure.

    Tools like PDFsam (evident in this app's uploaded filenames) can emit PDFs with
    non-standard xref tables / object streams that open fine in normal viewers but get
    rejected by Claude's stricter PDF parser ("Could not process PDF"). Reading with
    pypdf and rewriting the pages to a fresh file repairs that class of issue. Returns
    the path to the repaired copy, or None if the file can't be read/repaired at all
    (e.g. genuinely corrupted or encrypted).
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        logger.warning("pypdf_not_installed — cannot attempt PDF repair")
        return None
    try:
        reader = PdfReader(file_path, strict=False)
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


# ─────────────────────────────────────────────────────────────────────────────
# Core generation — streaming call with continuation support
# ─────────────────────────────────────────────────────────────────────────────

def _stream_once(
    client: anthropic.Anthropic,
    model_name: str,
    system_prompt: str,
    messages: list,
    has_files: bool,
):
    """Run one streamed Messages call and return the final Message. BLOCKING —
    wrap in run_in_executor. Streaming keeps the connection alive for the large
    max_tokens so we never hit an HTTP timeout.
    """
    kwargs = dict(
        model=model_name,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        thinking={"type": "adaptive"},          # accuracy — no temperature/top_p on Opus 4.8
        output_config={"effort": "high"},
        messages=messages,
    )
    if has_files:
        kwargs["betas"] = [FILES_BETA]
    with client.beta.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


async def _generate_with_continuation(
    *,
    client: anthropic.Anthropic,
    model_name: str,
    system_prompt: str,
    initial_content: list,
    has_files: bool,
    session_id: str,
    label: str = "",
) -> str:
    """Stream a response; if it stops at max_tokens, echo the model's own turn back
    and ask it to continue, stitching the pieces until it signals a natural stop.
    """
    loop = asyncio.get_running_loop()

    messages: list = [{"role": "user", "content": initial_content}]
    accumulated = ""
    mid_row = False  # did the LAST chunk stop in the middle of a table row?

    for attempt in range(MAX_CONTINUATIONS + 1):
        logger.info(
            "generate attempt=%d model=%s session=%s label=%s",
            attempt, model_name, session_id, label,
        )
        message = await loop.run_in_executor(
            _EXECUTOR, _stream_once, client, model_name, system_prompt, messages, has_files
        )
        stop = getattr(message, "stop_reason", None)
        text = _text_of(message)

        if stop == "refusal":
            raise RuntimeError("Model refused the request (safety)")
        if not text and stop != "max_tokens":
            raise RuntimeError("Empty response from model")

        # ── Smart stitch ────────────────────────────────────────────────────────
        if not accumulated:
            accumulated = text
        elif mid_row:
            accumulated += text
        else:
            accumulated += "\n" + text

        if stop != "max_tokens":
            logger.info(
                "generate_complete attempt=%d model=%s session=%s label=%s stop=%s",
                attempt, model_name, session_id, label, stop,
            )
            break

        if attempt == MAX_CONTINUATIONS:
            logger.warning(
                "max_continuations_reached model=%s session=%s label=%s",
                model_name, session_id, label,
            )
            break

        mid_row = _ends_mid_table_row(accumulated)
        logger.info(
            "truncated_continuing attempt=%d model=%s session=%s mid_row=%s",
            attempt, model_name, session_id, mid_row,
        )
        if mid_row:
            cont = (
                "Your previous response was cut off at the token limit while writing a "
                "table row. Resume that SAME row from the exact character where you "
                "stopped — output the remainder of the row first (no leading newline, "
                "no spaces, do NOT repeat any text already written, do NOT re-print the "
                "header), then continue with every remaining row and section. Never "
                "summarise or skip rows; keep going until the final piece."
            )
        else:
            cont = (
                "Your previous response was cut off at the token limit. Continue EXACTLY "
                "from where you stopped on a new line — do NOT restart, do NOT repeat any "
                "heading, row or content already written, do NOT re-print table headers. "
                "Complete every remaining row and section. Never summarise or skip rows; "
                "keep going until the final piece."
            )
        # Echo the model's own turn back UNCHANGED (preserves thinking blocks, which
        # Opus 4.8 requires on same-model continuation). This ends on a user turn, so
        # it is a valid continuation, not a forbidden assistant prefill.
        messages = [
            {"role": "user", "content": initial_content},
            {"role": "assistant", "content": message.content},
            {"role": "user", "content": cont},
        ]

    return accumulated


# ─────────────────────────────────────────────────────────────────────────────
# Single file-batch runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_single_batch(
    *,
    client: anthropic.Anthropic,
    model_name: str,
    system_prompt: str,
    user_text: str,
    batch_files: list[tuple[str, str]],
    batch_num: int,
    total_batches: int,
    session_id: str,
) -> str:
    """Upload one batch of files, run the take-off with continuation, clean up."""
    uploaded: list[tuple[str, str]] = []
    if batch_files:
        uploaded = await _upload_files(client, batch_files)
        if not uploaded:
            logger.warning("no_files_uploaded batch=%d session=%s", batch_num, session_id)

    base_user_text = user_text
    if total_batches > 1:
        base_user_text = (
            f"[File Batch {batch_num} of {total_batches}]\n\n"
            f"{user_text}\n\n"
            f"Analyse only the files in this file batch thoroughly."
        )

    # User content: the instruction text, then each file block. cache_control on the
    # last block caches the (stable) file prefix so continuations don't re-bill the PDFs.
    initial_content: list = [{"type": "text", "text": base_user_text}]
    for file_id, mime in uploaded:
        initial_content.append(_file_block(file_id, mime))
    if initial_content:
        last = initial_content[-1]
        last["cache_control"] = {"type": "ephemeral"}

    try:
        return await _generate_with_continuation(
            client=client,
            model_name=model_name,
            system_prompt=system_prompt,
            initial_content=initial_content,
            has_files=bool(uploaded),
            session_id=session_id,
            label=f"batch={batch_num}",
        )
    finally:
        if uploaded:
            _cleanup_files(client, uploaded)


async def _run_batch_with_fallback(
    *,
    client: anthropic.Anthropic,
    model_name: str,
    system_prompt: str,
    user_text: str,
    batch: list[tuple[str, str]],
    session_id: str,
) -> str:
    """Run one file batch. If Claude's API rejects it, isolate the cause instead of
    hard-failing the whole analysis:

      • A rejected batch of >1 files is bisected and each half retried recursively —
        this narrows down to whichever file(s) are actually at fault, and merges the
        (still lossless) results back into one output.
      • A single file that still fails on its own is repaired once (PDFs produced by
        tools like PDFsam can carry non-standard internal structure — see
        `_sanitize_pdf`) and retried before giving up on it.

    There is no pre-guessed size/page cap anywhere in this path — every fallback is a
    reaction to what Claude's API actually rejected.
    """
    try:
        return await _run_single_batch(
            client=client,
            model_name=model_name,
            system_prompt=system_prompt,
            user_text=user_text,
            batch_files=batch,
            batch_num=1,
            total_batches=1,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001
        if len(batch) > 1:
            mid = len(batch) // 2
            halves = [batch[:mid], batch[mid:]]
            logger.warning(
                "batch_rejected session=%s files=%d error=%s — isolating as %d smaller batches",
                session_id, len(batch), exc, len(halves),
            )
            outputs = [
                await _run_batch_with_fallback(
                    client=client,
                    model_name=model_name,
                    system_prompt=system_prompt,
                    user_text=user_text,
                    batch=half,
                    session_id=session_id,
                )
                for half in halves
            ]
            return merge_reports(outputs)

        # Down to a single file and it still failed — try one repair pass.
        file_path, mime = batch[0]
        if (mime or "").lower() == "application/pdf":
            repaired = _sanitize_pdf(file_path)
            if repaired:
                logger.warning(
                    "file_rejected_retrying_repaired session=%s path=%s",
                    session_id, file_path,
                )
                try:
                    return await _run_single_batch(
                        client=client,
                        model_name=model_name,
                        system_prompt=system_prompt,
                        user_text=user_text,
                        batch_files=[(repaired, mime)],
                        batch_num=1,
                        total_batches=1,
                        session_id=session_id,
                    )
                except Exception as exc2:  # noqa: BLE001
                    exc = exc2
                finally:
                    try:
                        os.remove(repaired)
                    except OSError:
                        pass

        raise RuntimeError(
            f"Claude could not process '{os.path.basename(file_path)}' even in "
            f"isolation (repair attempted). It may be corrupted, encrypted, or in a "
            f"non-standard format — re-export/re-save this file and try again. "
            f"Original error: {exc}"
        ) from exc


async def _run_batches(
    *,
    client: anthropic.Anthropic,
    model_name: str,
    system_prompt: str,
    user_text: str,
    batches: list[list[tuple[str, str]]],
    session_id: str,
) -> list[str]:
    """Run every top-level file batch (each with its own isolate-and-repair fallback)."""
    outputs: list[str] = []
    for i, batch in enumerate(batches, 1):
        logger.info(
            "file_batch_start batch=%d/%d model=%s session=%s files=%d",
            i, len(batches), model_name, session_id, len(batch),
        )
        output = await _run_batch_with_fallback(
            client=client,
            model_name=model_name,
            system_prompt=system_prompt,
            user_text=user_text,
            batch=batch,
            session_id=session_id,
        )
        outputs.append(output)
    return outputs


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_analysis(
    *,
    session_id: str,
    system_prompt: str,
    user_text: str,
    file_paths: Iterable[tuple[str, str]] = (),
    chunk_sections: bool = False,   # kept for interface compatibility (unused with Claude)
    single_pass: bool = False,
) -> tuple[str, str]:
    """
    Execute a Claude analysis with:
      • File batching   — large file sets split into 8-per-batch uploads
      • Continuation    — automatic continuation if output hits max_tokens
      • Deterministic merge — batches fused into ONE report, losslessly, with totals
                          recomputed by summation (report_merge)
      • Model fallback  — Opus 4.8 → Sonnet 5 on any error/refusal

    single_pass=True keeps the whole drawing set in ONE call (no split, no merge) so a
    mode with locked/derived totals (the Estimation engines) stays internally consistent.
    There is no pre-guessed size/page cap anywhere in this path: every batch (including
    the single_pass whole-set one) runs through `_run_batch_with_fallback`, which reacts
    to an actual rejection by bisecting the batch and, if it narrows down to one file
    that still fails, attempting a repair pass — always converging back to ONE final
    merged report rather than hard-failing the analysis.

    Returns (output_markdown, engine_display_label).
    """
    last_err: Exception | None = None
    file_paths_list = list(file_paths)

    if file_paths_list:
        batches = [file_paths_list] if single_pass else _build_batches(file_paths_list)
    else:
        batches = [[]]

    logger.info(
        "run_analysis_start session=%s total_files=%d total_file_batches=%d single_pass=%s",
        session_id, len(file_paths_list), len(batches), single_pass,
    )

    for model_name in MODEL_CHAIN:
        try:
            logger.info("model_attempt model=%s session=%s", model_name, session_id)
            client = _get_client()

            batch_outputs = await _run_batches(
                client=client,
                model_name=model_name,
                system_prompt=system_prompt,
                user_text=user_text,
                batches=batches,
                session_id=session_id,
            )

            if len(batch_outputs) == 1:
                final_output = batch_outputs[0]
            else:
                # Fuse batches into ONE report with the deterministic, lossless merge.
                final_output = merge_reports(batch_outputs)
                logger.info(
                    "deterministic_merge_used model=%s session=%s batches=%d",
                    model_name, session_id, len(batches),
                )

            # Navigable headings + strip the trailing tonnage-summary footer.
            final_output = normalize_section_headings(final_output)
            final_output = strip_summary_footer(final_output)

            logger.info(
                "run_analysis_complete model=%s session=%s file_batches=%d",
                model_name, session_id, len(batches),
            )
            return final_output, engine_label(model_name)

        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning(
                "model_failed model=%s session=%s error=%s",
                model_name, session_id, exc,
            )
            continue

    raise RuntimeError(
        f"All STRUCTMIND CORE tiers failed for session={session_id}. Last error: {last_err}"
    )
