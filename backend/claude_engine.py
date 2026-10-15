"""Claude engine — the one module that talks to the Anthropic API.

Everything above this layer (binder batching, take-off, report consolidation) works in
terms of `generate()` and `run_batch()`; nothing else imports the SDK.

  • Model: claude-opus-5-5 for every call (settings.anthropic_model — the one place the
    model is named). There is NO fallback model and no server-side model fallback: a
    request is only ever answered by Opus 5.5, and the served model id is returned so it
    is logged on every report.
  • Capacity queue: 429 / 5xx / 529 / connection errors are retried with exponential
    back-off for up to ANTHROPIC_CAPACITY_WAIT_MINUTES per call (the job shows "Waiting
    for model capacity" meanwhile); after that CapacityUnavailable is raised and the job
    is handed back to the queue by the caller — it never drops to another model.
  • Adaptive thinking with an explicit effort: "max" for take-off and estimation
    (settings.anthropic_effort_takeoff), settings.anthropic_effort for narrative reports,
    and 128K streamed output per turn.
  • Never truncated: output that is still incomplete after every continuation raises
    OutputTruncated and run_batch() splits the batch in two (by attachment, then by page
    range) so every table is completed in smaller pieces instead of being cut off.
  • Drawings go through the Files API (`client.files`) and are referenced by file_id with
    a human title ("D1 S-Structural.pdf — pages 26-50 of 120"); they are deleted after
    the batch. System prompt and attachments carry cache breakpoints, so parallel
    batches share the cached system prompt and continuations don't re-bill the PDFs.
  • Continuation: if a turn stops at max_tokens, the model's turn is appended unchanged
    (append-only history) and it is asked to continue; pieces are stitched, gluing a
    table row that was cut mid-row back together.
  • Recovery: an attachment Claude rejects is isolated by bisecting the batch, then a
    single bad PDF goes through the repair tiers (rewrite → rasterize) before giving up.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import anthropic

from binder import Unit
from config import settings
from pdf_repair import REPAIR_TIERS

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

MODEL: str = settings.anthropic_model or "claude-opus-5-5"
MODEL_CHAIN: list[str] = [MODEL]          # exactly one model; kept as a list for logging callers
logger.info("claude_model model=%s effort=%s takeoff_effort=%s", MODEL,
            settings.anthropic_effort, settings.anthropic_effort_takeoff)

ENGINE_LABELS: dict[str, str] = {
    "claude-opus-5-5": "STRUCTMIND CORE · PRO",
}
DEFAULT_ENGINE_LABEL = "STRUCTMIND CORE · PRO"

MAX_OUTPUT_TOKENS = 128_000
MAX_CONTINUATIONS = 12
CAPACITY_BACKOFF_START = 15.0     # seconds; doubles per retry
CAPACITY_BACKOFF_MAX = 300.0
_CAPACITY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

_EXECUTOR = ThreadPoolExecutor(max_workers=max(8, settings.max_parallel_calls * 2))
_slots: dict[int, asyncio.Semaphore] = {}


def engine_label(model: str) -> str:
    """Client-facing engine name. Reports only ever say STRUCTMIND CORE · PRO."""
    return ENGINE_LABELS.get(model or "", DEFAULT_ENGINE_LABEL)


def effort_for(kind: str) -> str:
    """Effort for a pipeline step: take-off / estimate at the highest effort."""
    if kind in ("takeoff", "estimate"):
        return settings.anthropic_effort_takeoff or "max"
    return settings.anthropic_effort or "high"


# A coroutine fn(label) the pipeline installs so a job waiting for capacity says so.
_WAIT_HOOK: contextvars.ContextVar = contextvars.ContextVar("structmind_wait_hook", default=None)


def set_wait_hook(fn) -> contextvars.Token:
    return _WAIT_HOOK.set(fn)


def reset_wait_hook(token: contextvars.Token) -> None:
    _WAIT_HOOK.reset(token)


async def _notify_wait(label: str) -> None:
    hook = _WAIT_HOOK.get()
    if hook is None:
        return
    try:
        await hook(label)
    except Exception:  # noqa: BLE001 — a progress update must never fail the call
        logger.debug("wait_hook_failed", exc_info=True)


def has_credentials() -> bool:
    return bool(settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))


def get_client() -> anthropic.Anthropic:
    if not has_credentials():
        raise EngineError("No LLM credentials configured. Set ANTHROPIC_API_KEY in the backend environment.")
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"),
        max_retries=3,
        timeout=60 * 60,
    )


def _call_slot() -> asyncio.Semaphore:
    """Process-wide cap on simultaneous Claude calls (one semaphore per event loop)."""
    loop = asyncio.get_running_loop()
    sem = _slots.get(id(loop))
    if sem is None:
        sem = asyncio.Semaphore(max(1, settings.max_parallel_calls))
        _slots[id(loop)] = sem
    return sem


# ─────────────────────────────────────────────────────────────────────────────
# Errors & results
# ─────────────────────────────────────────────────────────────────────────────

class EngineError(RuntimeError):
    """The engine could not produce an answer."""


class CapacityUnavailable(EngineError):
    """The model stayed unavailable (429/5xx/529) for the whole capacity wait. The job
    should be re-queued — never re-run on a different model."""


class OutputTruncated(EngineError):
    """The output was still incomplete after every continuation; split the batch."""


class DocumentRejected(EngineError):
    """Claude rejected the attachments themselves (bad/oversized PDF, too many pages)."""


_DOC_ERROR_HINTS = (
    "pdf", "document", "could not process", "image", "file", "page", "too large",
    "prompt is too long", "exceed", "media", "corrupt", "password", "encrypted",
)


def _is_capacity_error(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.OverloadedError,
                        anthropic.ServiceUnavailableError, anthropic.DeadlineExceededError,
                        anthropic.APIConnectionError)):
        return True
    return isinstance(exc, anthropic.APIStatusError) and getattr(exc, "status_code", 0) in _CAPACITY_STATUS


def _is_document_error(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.BadRequestError, anthropic.RequestTooLargeError,
                        anthropic.UnprocessableEntityError)):
        msg = str(getattr(exc, "message", "") or exc).lower()
        return any(h in msg for h in _DOC_ERROR_HINTS)
    return False


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def add_message(self, msg) -> None:
        u = getattr(msg, "usage", None)
        if not u:
            return
        self.input_tokens += int(getattr(u, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(u, "output_tokens", 0) or 0)
        self.cache_read_tokens += int(getattr(u, "cache_read_input_tokens", 0) or 0)
        self.cache_write_tokens += int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        self.calls += 1

    def merge(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.calls += other.calls

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "calls": self.calls,
        }


@dataclass
class GenResult:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)


# ─────────────────────────────────────────────────────────────────────────────
# Files
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Attachment:
    block: dict
    file_id: str | None = None


def _text_of_file(path: str) -> str:
    data = open(path, "rb").read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _upload_one(client: anthropic.Anthropic, u: Unit) -> list[Attachment]:
    if u.mime.startswith("text/"):
        return [Attachment({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": _text_of_file(u.path)},
            "title": u.title,
        })]
    with open(u.path, "rb") as fh:
        info = client.files.upload(file=(_upload_name(u), fh, u.mime))
    logger.info("upload_ready id=%s title=%s mb=%.1f", info.id, u.title, u.size_bytes / 1_048_576)
    if u.mime.startswith("image/"):
        out = []
        if u.caption:
            # Images carry no title field: the caption (page, tile position, px/ft) goes just before it.
            out.append(Attachment({"type": "text", "text": u.caption}))
        out.append(Attachment({"type": "image", "source": {"type": "file", "file_id": info.id}}, info.id))
        return out
    return [Attachment({"type": "document", "source": {"type": "file", "file_id": info.id}, "title": u.title}, info.id)]


def _upload_sync(client: anthropic.Anthropic, units: list[Unit]) -> list[Attachment]:
    """Upload the units of one batch (in parallel, order kept). Text units are inlined."""
    out: list[list[Attachment] | None] = [None] * len(units)
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(units)))) as pool:
        futures = {pool.submit(_upload_one, client, u): i for i, u in enumerate(units)}
        for fut, i in futures.items():
            try:
                out[i] = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
    flat = [a for group in out if group for a in group]
    if errors:
        _delete_sync(client, flat)
        raise errors[0]
    return flat


def _upload_name(u: Unit) -> str:
    base, ext = os.path.splitext(u.doc_name or os.path.basename(u.path))
    ext = ext or os.path.splitext(u.path)[1]
    if u.is_pdf and (u.page_start != 1 or u.page_end != u.doc_pages):
        return f"{base}_p{u.page_start}-{u.page_end}{ext}"
    return f"{base}{ext}"


def _delete_sync(client: anthropic.Anthropic, attachments: list[Attachment]) -> None:
    for a in attachments:
        if not a.file_id:
            continue
        try:
            client.files.delete(a.file_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("file_delete_error id=%s error=%s", a.file_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────────────────────────

def _text_of(message) -> str:
    return "".join(
        getattr(b, "text", "") or ""
        for b in (getattr(message, "content", None) or [])
        if getattr(b, "type", None) == "text"
    )


def _echo_blocks(message) -> list:
    """The assistant turn to append before asking for a continuation.

    Echoed unchanged (thinking blocks included — append-only history), except around a
    mid-output refusal fallback: blocks before the last `fallback` marker that belong to
    the declined model's reasoning are dropped, and the marker itself is omitted.
    """
    blocks = list(getattr(message, "content", None) or [])
    last_fb = max((i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"), default=-1)
    out = []
    for i, b in enumerate(blocks):
        t = getattr(b, "type", None)
        if t == "fallback":
            continue
        if i < last_fb and t in ("thinking", "redacted_thinking", "tool_use"):
            continue
        out.append(b)
    return out


def _stitch(text: str, chunk: str) -> str:
    """Join a continuation onto what was already written.

    A chunk that starts on a new line, or follows a complete markdown table row, gets a
    line break; otherwise the previous turn was cut mid-line and the pieces are glued.
    """
    if not text:
        return chunk
    if text.endswith("\n") or chunk.startswith("\n"):
        return text + chunk
    last = text.splitlines()[-1].strip()
    if last.startswith("|") and last.endswith("|") and len(last) > 1:
        return text + "\n" + chunk
    return text + chunk


def _stream_once(client: anthropic.Anthropic, model: str, system_blocks: list, messages: list, effort: str):
    kwargs = dict(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_blocks,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        messages=messages,
    )
    with client.beta.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


async def _stream_queued(client, model: str, system_blocks: list, messages: list, effort: str, label: str):
    """One model turn, waiting in the capacity queue instead of failing or switching model.

    The process-wide call slot is held only while a request is in flight, so a job that
    is backing off does not block other users' calls.
    """
    loop = asyncio.get_running_loop()
    deadline: float | None = None
    delay = CAPACITY_BACKOFF_START
    while True:
        try:
            async with _call_slot():
                return await loop.run_in_executor(_EXECUTOR, _stream_once, client, model, system_blocks, messages, effort)
        except Exception as exc:  # noqa: BLE001
            if not _is_capacity_error(exc) or _is_document_error(exc):
                raise
            now = time.monotonic()
            if deadline is None:
                deadline = now + max(0, settings.anthropic_capacity_wait_minutes) * 60
            remaining = deadline - now
            if remaining <= 0:
                raise CapacityUnavailable(
                    f"STRUCTMIND CORE capacity was unavailable for {settings.anthropic_capacity_wait_minutes} "
                    f"minutes ({label}): {exc}") from exc
            logger.warning("capacity_wait model=%s label=%s retry_in=%.0fs error=%s", model, label,
                           min(delay, remaining), exc)
            await _notify_wait(label)
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 2, CAPACITY_BACKOFF_MAX)


async def _generate_on_model(
    client: anthropic.Anthropic, model: str, system: str, content: list, label: str, effort: str,
) -> GenResult:
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages: list = [{"role": "user", "content": content}]
    usage = Usage()
    text = ""
    served_by = model
    for attempt in range(MAX_CONTINUATIONS + 1):
        logger.info("claude_call model=%s effort=%s label=%s attempt=%d", model, effort, label, attempt)
        msg = await _stream_queued(client, model, system_blocks, messages, effort, label)
        usage.add_message(msg)
        served_by = getattr(msg, "model", None) or served_by
        if served_by != model:
            logger.warning("served_model_differs requested=%s served=%s label=%s", model, served_by, label)
        stop = getattr(msg, "stop_reason", None)
        if stop == "refusal":
            raise EngineError(f"The model declined this request ({label}).")
        chunk = _text_of(msg)
        if not chunk and stop != "max_tokens":
            raise EngineError(f"Empty response from model ({label}).")

        text = _stitch(text, chunk)

        if stop != "max_tokens":
            break
        if attempt == MAX_CONTINUATIONS:
            logger.warning("max_continuations_reached model=%s label=%s", model, label)
            raise OutputTruncated(f"Output still incomplete after {MAX_CONTINUATIONS} continuations ({label}).")
        cont = (
            "Your previous message was cut off at the output limit. Continue EXACTLY where it "
            "stopped — if you stopped mid-line, output the rest of that line first with no "
            "leading newline. Do not repeat anything already written, do not restart a section, "
            "do not re-print table headers. Keep going until the deliverable is complete."
        )
        messages = messages + [
            {"role": "assistant", "content": _echo_blocks(msg)},
            {"role": "user", "content": cont},
        ]
    return GenResult(text=text, model=served_by, usage=usage)


async def generate(
    *, system: str, content: list, label: str, effort: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> GenResult:
    """Run one deliverable on the configured model (Opus 5.5 only).

    Raises DocumentRejected when the attachments themselves were refused (the caller
    isolates/repairs them), CapacityUnavailable when the model stayed busy for the whole
    capacity wait (the caller re-queues the job), OutputTruncated when the output could
    not be completed (the caller splits the batch) and EngineError otherwise.
    """
    client = client or get_client()
    effort = effort or settings.anthropic_effort or "high"
    try:
        return await _generate_on_model(client, MODEL, system, content, label, effort)
    except EngineError:
        raise
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise EngineError(f"The Anthropic API key was rejected: {exc}") from exc
    except anthropic.NotFoundError as exc:
        raise EngineError(f"The configured model is not available to this API key ({label}): {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        msg = str(getattr(exc, "message", "") or exc)
        if "usage limit" in msg.lower() or "credit balance" in msg.lower():
            # Account-level spend cap / no credit: nothing to retry until the owner adds credit.
            raise EngineError(
                "The Anthropic API account has reached its usage limit or has no credit left. "
                "Raise the spend limit or add credit at console.anthropic.com (Settings → Limits / Billing), "
                "then click Try again.") from exc
        if _is_document_error(exc):
            raise DocumentRejected(msg) from exc
        logger.warning("model_failed model=%s label=%s error=%s", MODEL, label, exc)
        raise EngineError(f"STRUCTMIND CORE could not complete this step ({label}): {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Batches with attachment recovery
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BatchOutput:
    text: str
    units: list[Unit]
    model: str
    usage: Usage


async def _run_units(client, system: str, header: str, units: list[Unit], label: str, effort: str | None) -> GenResult:
    loop = asyncio.get_running_loop()
    attachments: list[Attachment] = []
    if units:
        attachments = await loop.run_in_executor(_EXECUTOR, _upload_sync, client, units)
    try:
        content: list = [{"type": "text", "text": header}]
        content.extend(a.block for a in attachments)
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
        return await generate(system=system, content=content, label=label, effort=effort, client=client)
    finally:
        if attachments:
            await loop.run_in_executor(_EXECUTOR, _delete_sync, client, attachments)


async def run_batch(
    *, system: str, header_for, units: list[Unit], label: str, effort: str | None = None,
) -> list[BatchOutput]:
    """Run one batch. If Claude rejects the attachments, bisect to isolate the culprit and
    repair a single bad PDF; returns one output per sub-batch that was actually run.

    header_for(units) builds the user-turn text for whichever subset is being sent, so a
    bisected half still tells the model exactly which pages it holds.
    """
    client = get_client()
    try:
        res = await _run_units(client, system, header_for(units), units, label, effort)
        return [BatchOutput(res.text, units, res.model, res.usage)]
    except (DocumentRejected, OutputTruncated) as exc:
        truncated = isinstance(exc, OutputTruncated)
        if len(units) > 1:
            mid = len(units) // 2
            logger.warning("batch_%s label=%s units=%d — bisecting (%s)",
                           "truncated" if truncated else "rejected", label, len(units), exc)
            first = await run_batch(system=system, header_for=header_for, units=units[:mid],
                                    label=f"{label}a", effort=effort)
            second = await run_batch(system=system, header_for=header_for, units=units[mid:],
                                     label=f"{label}b", effort=effort)
            return first + second
        unit = units[0]
        if unit.is_pdf and unit.pages > 1:
            halves = _halve_pdf_unit(unit)
            if halves:
                logger.warning("unit_%s label=%s — splitting %s", "truncated" if truncated else "rejected",
                               label, unit.title)
                try:
                    first = await run_batch(system=system, header_for=header_for, units=[halves[0]],
                                            label=f"{label}a", effort=effort)
                    second = await run_batch(system=system, header_for=header_for, units=[halves[1]],
                                             label=f"{label}b", effort=effort)
                    return first + second
                finally:
                    for h in halves:
                        try:
                            os.remove(h.path)
                        except OSError:
                            pass
        if truncated:
            raise EngineError(f"'{unit.title}' produced more output than can be completed in one call "
                              "even after splitting it to a single page.") from exc
        if unit.is_pdf:
            return [await _repair_and_run(client, system, header_for, unit, label, effort, exc)]
        raise DocumentRejected(
            f"'{unit.doc_name}' could not be read by the engine ({exc}). Re-export it and try again."
        ) from exc


def _halve_pdf_unit(unit: Unit) -> list[Unit] | None:
    from binder import _open_pdf, _write_pdf_range  # local import: binder owns PDF writing

    try:
        reader = _open_pdf(unit.path)
        if reader is None or len(reader.pages) < 2:
            return None
        n = len(reader.pages)
        mid = n // 2
        out: list[Unit] = []
        for a, b in ((1, mid), (mid + 1, n)):
            fd, path = tempfile.mkstemp(suffix=".half.pdf")
            os.close(fd)
            size = _write_pdf_range(reader, a, b, path)
            out.append(Unit(path, unit.mime, unit.doc_index, unit.doc_name, unit.doc_pages,
                            unit.page_start + a - 1, unit.page_start + b - 1, size, is_temp=True))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("unit_halve_failed path=%s error=%s", unit.path, exc)
        return None


async def _repair_and_run(client, system, header_for, unit: Unit, label, effort, original_exc) -> BatchOutput:
    loop = asyncio.get_running_loop()
    current = unit.path
    temp: list[str] = []
    last_exc: Exception = original_exc
    try:
        for repair_fn, tier in REPAIR_TIERS:
            repaired = await loop.run_in_executor(_EXECUTOR, repair_fn, current)
            if not repaired:
                continue
            temp.append(repaired)
            fixed = Unit(repaired, unit.mime, unit.doc_index, unit.doc_name, unit.doc_pages,
                         unit.page_start, unit.page_end, os.path.getsize(repaired), is_temp=True)
            logger.warning("unit_retry_%s label=%s title=%s", tier, label, unit.title)
            try:
                res = await _run_units(client, system, header_for([fixed]), [fixed], f"{label}-{tier}", effort)
                return BatchOutput(res.text, [unit], res.model, res.usage)
            except DocumentRejected as exc:
                last_exc = exc
                current = repaired
        raise DocumentRejected(
            f"'{unit.title}' could not be read even after repair and rasterization. It may be "
            f"corrupted or encrypted — re-export it to PDF and upload again. ({last_exc})"
        )
    finally:
        for p in temp:
            try:
                os.remove(p)
            except OSError:
                pass


async def gather_limited(coros, limit: int):
    """asyncio.gather with at most `limit` coroutines in flight, results in input order.

    If any coroutine fails, the ones not yet finished are cancelled so a failed binder
    stops spending API time on batches whose results would be thrown away.
    """
    sem = asyncio.Semaphore(max(1, limit))

    async def _wrap(c):
        async with sem:
            return await c

    tasks = [asyncio.ensure_future(_wrap(c)) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
