"""Confidential detailing rate bands — the ONLY place hourly detailing rates live.

Estimation Pro converts the locked hours to a project cost with a regional band
(currency, low, high). The band is read once, inside the cost step of
`estimation.detailing_estimate.from_takeoff`; only the rounded project cost leaves that
step. No report, estimate dict, log line, API payload or prompt carries a rate value —
the report prints "[CONFIDENTIAL]" in the rate field. The quality gate imports
`rate_values()` to scan every rendered report for a leaked rate.

Configuration (later sources override earlier ones, region by region):
  1. built-in defaults (below)
  2. env DETAILING_RATES_FILE — path to a JSON file
  3. env DETAILING_RATES_JSON — inline JSON
Both JSON sources take the same shape, either form per region:
  {"USA": ["USD", 18, 28], "CANADA": {"currency": "CAD", "low": 24, "high": 38}}
An optional top-level {"rates": {...}} wrapper is accepted for files. Invalid entries are
ignored (the default stays) and logged WITHOUT their values; this module never raises
on bad configuration.

Public API (nothing else is meant to be imported):
  detailing_rates(region) -> (currency, low, high)
  rate_values()           -> every configured rate, for the quality-gate scan
"""
from __future__ import annotations

import json
import logging
import math
import os
import re

logger = logging.getLogger(__name__)

ENV_JSON = "DETAILING_RATES_JSON"
ENV_FILE = "DETAILING_RATES_FILE"

_DEFAULT_REGION = "USA"
_DEFAULTS: dict[str, tuple[str, float, float]] = {
    "USA": ("USD", 18.0, 28.0),
    "CANADA": ("CAD", 24.0, 38.0),
    "AUSTRALIA": ("AUD", 27.0, 42.0),
}
_ALIASES = {
    "US": "USA", "USA": "USA", "UNITEDSTATES": "USA", "UNITEDSTATESOFAMERICA": "USA",
    "CA": "CANADA", "CAN": "CANADA", "CANADA": "CANADA",
    "AU": "AUSTRALIA", "AUS": "AUSTRALIA", "AUSTRALIA": "AUSTRALIA",
}
_MAX_FILE_BYTES = 64 * 1024
_MAX_RATE = 100_000.0          # sanity ceiling — anything above is a typo, not a rate
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

_cache: dict[tuple, dict[str, tuple[str, float, float]]] = {}


# ─── Parsing ─────────────────────────────────────────────────────────────────

def _region_key(region) -> str:
    """'usa', 'U.S.', 'United States' → 'USA'; unknown names keep their compact upper form."""
    s = re.sub(r"[^A-Z]", "", str(region or "").upper())
    return _ALIASES.get(s, s)


def _rate(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and 0 < f <= _MAX_RATE else None


def _entry(value) -> tuple[str, float, float] | None:
    if isinstance(value, dict):
        cur, low, high = value.get("currency"), value.get("low"), value.get("high")
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        cur, low, high = value
    else:
        return None
    cur = str(cur or "").strip().upper()
    lo, hi = _rate(low), _rate(high)
    if not _CURRENCY_RE.match(cur) or lo is None or hi is None or lo > hi:
        return None
    return cur, lo, hi


def _merge(table: dict, raw, source: str) -> None:
    if isinstance(raw, dict) and isinstance(raw.get("rates"), dict):
        raw = raw["rates"]
    if not isinstance(raw, dict):
        logger.warning("detailing rates: %s ignored (expected a JSON object)", source)
        return
    for region, value in raw.items():
        key = _region_key(region)
        entry = _entry(value)
        if not key or entry is None:
            logger.warning("detailing rates: %s entry for region %r ignored (invalid band)", source, key or "?")
            continue
        table[key] = entry


def _file_signature(path: str) -> tuple:
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, None, None)


def _read_file(path: str):
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            logger.warning("detailing rates: %s ignored (file larger than %d bytes)", ENV_FILE, _MAX_FILE_BYTES)
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError):
        logger.warning("detailing rates: %s ignored (unreadable or not valid JSON)", ENV_FILE)
        return None


def _table() -> dict[str, tuple[str, float, float]]:
    """Effective rate table; cached until either env var or the file changes."""
    env_json = os.environ.get(ENV_JSON, "").strip()
    env_file = os.environ.get(ENV_FILE, "").strip()
    sig = (env_json, _file_signature(env_file) if env_file else None)
    hit = _cache.get(sig)
    if hit is not None:
        return hit

    table = dict(_DEFAULTS)
    if env_file:
        raw = _read_file(env_file)
        if raw is not None:
            _merge(table, raw, ENV_FILE)
    if env_json:
        try:
            _merge(table, json.loads(env_json), ENV_JSON)
        except ValueError:
            logger.warning("detailing rates: %s ignored (not valid JSON)", ENV_JSON)
    _cache.clear()
    _cache[sig] = table
    return table


# ─── Public API ──────────────────────────────────────────────────────────────

def detailing_rates(region: str) -> tuple[str, float, float]:
    """(currency, low, high) per hour for a region; unknown regions use the USA band."""
    table = _table()
    return table.get(_region_key(region)) or table.get(_DEFAULT_REGION) or _DEFAULTS[_DEFAULT_REGION]


def rate_values() -> set[float]:
    """Every configured rate (low and high, all regions) — for the quality-gate scan only."""
    return {v for _cur, lo, hi in _table().values() for v in (lo, hi)}
