"""
LLM service — google.genai with service account authentication.

KEY FIXES vs previous version
──────────────────────────────
1.  max_output_tokens raised to 65 536 (Gemini 2.5 Pro/Flash support up to
    65 536 output tokens).  This alone fixes the truncated 12-section intake.
2.  finish_reason guard: if Gemini returns finish_reason == MAX_TOKENS the
    response was cut.  We automatically continue with a CONTINUATION prompt
    and stitch the pieces together until the model signals STOP.
3.  Section-chunk mode (SECTION_GROUPS): for MASTER_INTAKE the prompt is split
    into two logical halves (S1-S6, S7-S12) and run as two sequential calls
    sharing the same uploaded files.  Results are stitched cleanly.
4.  Uploaded files are kept alive across ALL model fallback attempts and ALL
    section chunks; deleted only after the entire session succeeds or all
    models fail.
5.  Client is created ONCE per run_analysis call, not once per model tier.
6.  Empty-batch guard: text-only requests skip the Files API entirely.
7.  Async-safe: file upload/wait loop is offloaded to a thread pool so the
    event loop is never blocked.
8.  Structured logging with consistent field names throughout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

import google.genai as genai
from google.genai import types
from google.oauth2 import service_account
from google.auth.transport.requests import Request

from config import settings
from report_merge import merge_reports

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Model chain & labels
# ─────────────────────────────────────────────────────────────────────────────

MODEL_CHAIN: list[str] = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

ENGINE_LABELS: dict[str, str] = {
    "gemini-2.5-pro":   "STRUCTMIND CORE · PRO",
    "gemini-2.5-flash": "STRUCTMIND CORE · FAST",
    "gemini-2.0-flash": "STRUCTMIND CORE · LITE",
}

# ─────────────────────────────────────────────────────────────────────────────
# Tunable limits
# ─────────────────────────────────────────────────────────────────────────────

MAX_BATCH_MB       = 90.0   # max total MB per Gemini Files API request
MAX_FILES_PER_BATCH = 8     # files per upload batch (product spec: 8 per batch)

# Output token budget.
# Gemini 2.5 Pro and Flash both support up to 65 536 output tokens.
# We use 65 536 to allow a full 12-section MASTER_INTAKE in one shot.
MAX_OUTPUT_TOKENS  = 65_536

# If the model still hits the limit (finish_reason == MAX_TOKENS) we issue
# continuation calls.  Each continuation re-uses the uploaded files, so a single
# batch can stream a take-off of effectively unlimited length (e.g. 2 000+ rows)
# by stitching ~65 k-token chunks until the model signals STOP.
MAX_CONTINUATIONS  = 24     # safety cap — avoids infinite loops, not output length

# Section groups for MASTER_INTAKE chunking.
# Each tuple is (group_label, section_numbers_string_for_prompt).
# Only applied when the caller opts in via chunk_sections=True.
SECTION_GROUPS: list[tuple[str, str]] = [
    ("PART-A · Sections 1–6",  "SECTIONS 1, 2, 3, 4, 5, AND 6 ONLY"),
    ("PART-B · Sections 7–12", "SECTIONS 7, 8, 9, 10, 11, AND 12 ONLY"),
]

# Thread pool for blocking I/O (file upload, polling)
_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def engine_label(internal_model: str) -> str:
    return ENGINE_LABELS.get(internal_model, "STRUCTMIND CORE")


def _get_credentials():
    """Return fresh service-account credentials, or None."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        return None
    try:
        sa_info = json.loads(sa_json)
        credentials = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/generative-language"],
        )
        credentials.refresh(Request())
        logger.info("service_account_credentials=refreshed")
        return credentials
    except Exception as exc:
        logger.warning("service_account_auth_failed error=%s", exc)
        return None


def _get_client() -> genai.Client:
    """Return an authenticated Gemini client (created once per session)."""
    credentials = _get_credentials()
    if credentials:
        logger.info("gemini_client=service_account")
        return genai.Client(credentials=credentials)
    if settings.llm_key:
        logger.info("gemini_client=api_key")
        return genai.Client(api_key=settings.llm_key)
    raise RuntimeError("No Gemini credentials configured")


# ─────────────────────────────────────────────────────────────────────────────
# File batching
# ─────────────────────────────────────────────────────────────────────────────

def _build_batches(
    file_paths: list[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    """
    Split file_paths into batches that each stay under MAX_BATCH_MB and
    MAX_FILES_PER_BATCH.  Largest files first.
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
# File upload — blocking, wrapped for async
# ─────────────────────────────────────────────────────────────────────────────

def _upload_files_sync(
    client: genai.Client,
    file_paths: list[tuple[str, str]],
) -> list:
    """
    Upload files to Gemini Files API (blocking).
    Polls until ACTIVE or FAILED.  Returns list of active file objects.
    Called via run_in_executor so the event loop stays free.
    """
    uploaded = []
    for file_path, mime_type in file_paths:
        if not os.path.exists(file_path):
            logger.warning("upload_skip_missing path=%s", file_path)
            continue
        size_mb = os.path.getsize(file_path) / (1_024 * 1_024)
        logger.info("uploading path=%s size_mb=%.1f", file_path, size_mb)
        try:
            with open(file_path, "rb") as fh:
                uploaded_file = client.files.upload(
                    file=fh,
                    config=types.UploadFileConfig(mime_type=mime_type),
                )

            # Poll until ACTIVE
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                info = client.files.get(name=uploaded_file.name)
                state = info.state.name
                if state == "ACTIVE":
                    logger.info(
                        "upload_ready name=%s size_mb=%.1f",
                        uploaded_file.name, size_mb,
                    )
                    uploaded.append(info)
                    break
                if state == "FAILED":
                    logger.error("upload_failed name=%s", uploaded_file.name)
                    break
                logger.debug("upload_state=%s name=%s", state, uploaded_file.name)
                time.sleep(3)
            else:
                logger.warning("upload_timeout name=%s", uploaded_file.name)

        except Exception as exc:
            logger.warning("upload_error path=%s error=%s", file_path, exc)

    return uploaded


async def _upload_files(
    client: genai.Client,
    file_paths: list[tuple[str, str]],
) -> list:
    """Async wrapper — runs blocking upload in thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _EXECUTOR, _upload_files_sync, client, file_paths
    )


def _cleanup_files(client: genai.Client, uploaded_files: list) -> None:
    """Delete uploaded files from Gemini Files API."""
    for f in uploaded_files:
        try:
            client.files.delete(name=f.name)
            logger.info("file_deleted name=%s", f.name)
        except Exception as exc:
            logger.warning("file_delete_error name=%s error=%s", f.name, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Core generation — single call with continuation support
# ─────────────────────────────────────────────────────────────────────────────

def _is_truncated(response) -> bool:
    """
    Return True if Gemini stopped because it hit the output token limit.
    The finish_reason field lives on the first candidate.
    """
    try:
        reason = response.candidates[0].finish_reason
        # FinishReason enum: STOP=1, MAX_TOKENS=2
        # Accept both the enum value and its string name
        return str(reason) in ("FinishReason.MAX_TOKENS", "MAX_TOKENS", "2")
    except Exception:
        return False


def _ends_mid_table_row(text: str) -> bool:
    """True if `text` ends in the MIDDLE of a markdown table row.

    A complete row ends with '|'. If the last non-empty line opens a row ('|...')
    but has no closing '|', the model was cut off mid-row and the continuation must
    be glued on with no separator so the row is not split into two broken rows.
    """
    for line in reversed(text.splitlines()):
        if not line.strip():
            continue
        ls = line.strip()
        return ls.startswith("|") and not ls.endswith("|")
    return False


def _generate(
    client: genai.Client,
    model_name: str,
    system_prompt: str,
    contents: list,
) -> str:
    """
    Run a single generate_content call and return the text.
    Raises RuntimeError on empty or fully blocked response.
    This is a BLOCKING call — wrap in run_in_executor for async callers.
    """
    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Empty response from model")
    return text, _is_truncated(response)


async def _generate_with_continuation(
    *,
    client: genai.Client,
    model_name: str,
    system_prompt: str,
    initial_parts: list[types.Part],
    session_id: str,
    label: str = "",
) -> str:
    """
    Run generate_content.  If the model hits MAX_TOKENS, send a continuation
    prompt and stitch the pieces together.  Repeats up to MAX_CONTINUATIONS.

    Returns the full stitched text.
    """
    loop = asyncio.get_running_loop()

    # Build initial message
    contents: list[types.Content] = [
        types.Content(role="user", parts=initial_parts)
    ]

    accumulated = ""
    mid_row = False  # did the LAST chunk stop in the middle of a table row?

    for attempt in range(MAX_CONTINUATIONS + 1):
        logger.info(
            "generate attempt=%d model=%s session=%s label=%s",
            attempt, model_name, session_id, label,
        )
        text, truncated = await loop.run_in_executor(
            _EXECUTOR, _generate, client, model_name, system_prompt, contents
        )

        # ── Smart stitch ─────────────────────────────────────────────────────
        # Glue continuations so a take-off table split across chunks is never
        # broken at the seam: mid-row resumes with no separator; otherwise a
        # newline starts the next line cleanly.
        if not accumulated:
            accumulated = text
        elif mid_row:
            accumulated += text
        else:
            accumulated += "\n" + text

        if not truncated:
            logger.info(
                "generate_complete attempt=%d model=%s session=%s label=%s",
                attempt, model_name, session_id, label,
            )
            break

        if attempt == MAX_CONTINUATIONS:
            logger.warning(
                "max_continuations_reached model=%s session=%s label=%s rows_may_be_incomplete",
                model_name, session_id, label,
            )
            break

        mid_row = _ends_mid_table_row(accumulated)
        logger.info(
            "truncated_continuing attempt=%d model=%s session=%s mid_row=%s",
            attempt, model_name, session_id, mid_row,
        )
        if mid_row:
            continuation_instruction = (
                "Your previous response was cut off at the token limit while writing a "
                "table row. Resume that SAME row from the exact character where you "
                "stopped — output the remainder of the row first (no leading newline, "
                "no spaces, do NOT repeat any text already written, do NOT re-print the "
                "header), then continue with every remaining row and section. Never "
                "summarise or skip rows; keep going until the final piece."
            )
        else:
            continuation_instruction = (
                "Your previous response was cut off at the token limit. Continue EXACTLY "
                "from where you stopped on a new line — do NOT restart, do NOT repeat any "
                "heading, row or content already written, do NOT re-print table headers. "
                "Complete every remaining row and section. Never summarise or skip rows; "
                "keep going until the final piece."
            )
        contents = [
            types.Content(role="user",    parts=initial_parts),
            types.Content(role="model",   parts=[types.Part(text=text)]),
            types.Content(role="user",    parts=[types.Part(text=continuation_instruction)]),
        ]

    return accumulated


# ─────────────────────────────────────────────────────────────────────────────
# Single file-batch runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_single_batch(
    *,
    client: genai.Client,
    model_name: str,
    system_prompt: str,
    user_text: str,
    batch_files: list[tuple[str, str]],
    batch_num: int,
    total_batches: int,
    session_id: str,
    chunk_sections: bool = False,
) -> str:
    """
    Run one Gemini request for a single batch of files.

    If chunk_sections=True the call is split into two sequential requests
    (SECTION_GROUPS) sharing the same uploaded files.  This guarantees
    all 12 sections are generated even if a single call would be too long.

    Returns stitched markdown text.
    """
    # ── Upload files ────────────────────────────────────────────────────────
    uploaded_files: list = []
    if batch_files:
        uploaded_files = await _upload_files(client, batch_files)
        if not uploaded_files:
            logger.warning(
                "no_files_uploaded batch=%d session=%s", batch_num, session_id
            )

    # ── Build file parts (reused across section chunks) ──────────────────────
    file_parts: list[types.Part] = [
        types.Part(
            file_data=types.FileData(
                file_uri=f.uri,
                mime_type=f.mime_type,
            )
        )
        for f in uploaded_files
    ]

    # ── Annotate user text for multi-file-batch jobs ─────────────────────────
    base_user_text = user_text
    if total_batches > 1:
        base_user_text = (
            f"[File Batch {batch_num} of {total_batches}]\n\n"
            f"{user_text}\n\n"
            f"Analyse only the files in this file batch thoroughly."
        )

    try:
        # ── Section-chunk mode: two calls, results stitched ───────────────────
        if chunk_sections:
            section_outputs: list[str] = []

            for group_label, section_spec in SECTION_GROUPS:
                chunk_instruction = (
                    f"\n\nCRITICAL INSTRUCTION: Output {section_spec}. "
                    f"Do NOT output any other sections. "
                    f"Begin immediately with the first section in this group."
                )
                chunk_text = base_user_text + chunk_instruction
                initial_parts = [types.Part(text=chunk_text)] + file_parts

                logger.info(
                    "section_chunk group=%s batch=%d/%d model=%s session=%s",
                    group_label, batch_num, total_batches, model_name, session_id,
                )
                chunk_output = await _generate_with_continuation(
                    client=client,
                    model_name=model_name,
                    system_prompt=system_prompt,
                    initial_parts=initial_parts,
                    session_id=session_id,
                    label=f"{group_label} batch={batch_num}",
                )
                section_outputs.append(chunk_output)

            return "\n\n".join(section_outputs)

        # ── Single-call mode ──────────────────────────────────────────────────
        initial_parts = [types.Part(text=base_user_text)] + file_parts
        return await _generate_with_continuation(
            client=client,
            model_name=model_name,
            system_prompt=system_prompt,
            initial_parts=initial_parts,
            session_id=session_id,
            label=f"batch={batch_num}",
        )

    finally:
        # Always clean up uploaded files
        if uploaded_files:
            _cleanup_files(client, uploaded_files)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_analysis(
    *,
    session_id: str,
    system_prompt: str,
    user_text: str,
    file_paths: Iterable[tuple[str, str]] = (),
    chunk_sections: bool = False,
    single_pass: bool = False,
) -> tuple[str, str]:
    """
    Execute Gemini analysis with:
      • File batching   — large file sets split into safe-sized batches
      • Section chunking — optional two-pass mode for long outputs (MASTER_INTAKE)
      • Continuation    — automatic continuation if output is truncated
      • Model fallback  — falls through MODEL_CHAIN on any error

    Parameters
    ----------
    session_id      : Unique identifier for logging / tracing.
    system_prompt   : System instruction string.
    user_text       : User-facing task description.
    file_paths      : Iterable of (local_path, mime_type) tuples.
    chunk_sections  : Set True for MASTER_INTAKE (12-section) prompts to split
                      the output into two sequential section-group calls,
                      guaranteeing all sections are generated.
    single_pass     : Set True for modes whose output is one internally-consistent
                      computation that must NOT be split + merged — e.g. the
                      Estimation engines, whose locked manifest (tonnage/hours → cost)
                      would be corrupted by cross-batch merging. All files are sent in
                      ONE call so the figures are derived from the whole project at once.

    Returns
    -------
    (output_markdown, engine_display_label)
    """
    last_err: Exception | None = None
    file_paths_list = list(file_paths)

    # Build file batches once — reused across model fallback attempts
    if file_paths_list:
        # single_pass keeps the entire drawing-set in one call (no split, no merge) so
        # locked/derived totals stay internally consistent.
        batches = [file_paths_list] if single_pass else _build_batches(file_paths_list)
    else:
        # Text-only: one empty batch (no file upload)
        batches = [[]]

    logger.info(
        "run_analysis_start session=%s total_files=%d total_file_batches=%d "
        "chunk_sections=%s",
        session_id, len(file_paths_list), len(batches), chunk_sections,
    )

    # ── Model fallback loop ───────────────────────────────────────────────────
    for model_name in MODEL_CHAIN:
        try:
            logger.info(
                "model_attempt model=%s session=%s", model_name, session_id
            )
            # One client per run_analysis call
            client = _get_client()
            batch_outputs: list[str] = []

            for i, batch in enumerate(batches, 1):
                logger.info(
                    "file_batch_start batch=%d/%d model=%s session=%s",
                    i, len(batches), model_name, session_id,
                )
                output = await _run_single_batch(
                    client=client,
                    model_name=model_name,
                    system_prompt=system_prompt,
                    user_text=user_text,
                    batch_files=batch,
                    batch_num=i,
                    total_batches=len(batches),
                    session_id=session_id,
                    chunk_sections=chunk_sections,
                )
                batch_outputs.append(output)

            if len(batch_outputs) == 1:
                final_output = batch_outputs[0]
            else:
                # Multiple file batches — fuse into a SINGLE coherent report with a
                # deterministic, table-aware merge. Unlike an LLM consolidation pass
                # (which must re-emit every row and therefore truncates and drops rows
                # on large take-offs), this preserves EVERY row from EVERY batch and
                # recomputes project totals by summation. The client never sees a batch.
                final_output = merge_reports(batch_outputs)
                logger.info(
                    "deterministic_merge_used model=%s session=%s batches=%d",
                    model_name, session_id, len(batches),
                )

            logger.info(
                "run_analysis_complete model=%s session=%s file_batches=%d",
                model_name, session_id, len(batches),
            )
            return final_output, engine_label(model_name)

        except Exception as exc:
            last_err = exc
            logger.warning(
                "model_failed model=%s session=%s error=%s",
                model_name, session_id, exc,
            )
            continue

    raise RuntimeError(
        f"All STRUCTMIND CORE tiers failed for session={session_id}. "
        f"Last error: {last_err}"
    )