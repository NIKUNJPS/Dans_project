"""AI-driven drawing take-off for estimation.

Thin wrapper over the itemized take-off engine (``estimation.mto``). Kept as the stable
entry point used by the estimation route and the project tonnage lock. The return shape
is unchanged — ``(data, engine_label)`` — but ``data`` is now the full structured
take-off (line items, category rollup, deterministic tonnage, confidence, assumptions,
RFIs) rather than a single bulk figure.
"""
from __future__ import annotations

import logging
from typing import Iterable

from estimation.mto import run_itemized_mto

logger = logging.getLogger(__name__)


# Map an estimation country code to the take-off jurisdiction whose unit-weight
# catalogue applies. Anything else defaults to the AISC (USA) tables.
_COUNTRY_TO_JURISDICTION = {
    "USA": "USA", "US": "USA",
    "CAN": "CANADA", "CANADA": "CANADA",
    "AUS": "AUSTRALIA", "AU": "AUSTRALIA", "AUSTRALIA": "AUSTRALIA",
}


def country_to_jurisdiction(country_code: str) -> str:
    return _COUNTRY_TO_JURISDICTION.get((country_code or "USA").upper(), "USA")


async def extract_quantities(
    *,
    role: str,
    session_id: str,
    file_paths: Iterable[tuple[str, str]],
    country_code: str = "USA",
) -> tuple[dict, str]:
    """Run the itemized take-off and return ``(structured_data, engine_label)``."""
    if not file_paths:
        raise ValueError("Upload at least one drawing to run AI estimation.")
    return await run_itemized_mto(
        role=role,
        session_id=session_id,
        file_paths=file_paths,
        jurisdiction=country_to_jurisdiction(country_code),
    )
