"""AI-driven drawing analysis for estimation.

Given uploaded drawings + a role, asks STRUCTMIND CORE to extract the structural
quantities needed for cost estimation.

Returns a deterministic dict that the calculator can consume.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from config import settings
from gemini_service import MODEL_CHAIN, engine_label, _get_client

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────────────────────

FABRICATOR_EXTRACT_PROMPT = """
You are STRUCTMIND CORE, a senior structural-steel fabricator's estimator.

Your task: read EVERY attached structural drawing, BOM, schedule and document and
compute the total fabricated tonnage with a disciplined member-by-member take-off.

METHOD (perform internally, do not narrate):
  1. Scan every sheet and every document. Use the BOM / member schedule where present;
     otherwise quantify members from framing plans and details.
  2. For each member: identify profile, quantity and length, then apply the published
     AISC / standard unit weight (kg/m) for that profile. Plates and bars: compute from
     volume × 7,850 kg/m³. Convert all weights to metric tons.
  3. Add a 3.0% allowance for bolts, welds, connection plates and accessories.
  4. SUM every member. Do not sample, round members away, or estimate a bulk figure —
     the tonnage must be the sum of the actual take-off.
  5. Be exhaustive: include secondary steel, miscellaneous, embeds and anchors.

Round the final tonnage to exactly two decimal places.

Return ONLY valid JSON (no markdown, no commentary):

{
  "tonnage": 184.52,
  "members_counted": 250,
  "primary_material": "A992 W-shapes",
  "drawings_seen": 12,
  "accessory_allowance_pct": 3.0,
  "notes": "Member-by-member take-off from BOM and framing plans; AISC unit weights applied."
}
"""

DETAILER_EXTRACT_PROMPT = """
You are STRUCTMIND CORE, a senior structural-steel detailing lead.

Your ONLY task:
Read the attached structural drawings and estimate the detailing workload in HOURS.

Estimate total_hours as the realistic effort for a competent detailer to produce
the full fabrication-ready model and shop drawings for this scope, including:
  - production/shop drawings (modelling + drawing time),
  - connection detailing,
  - checking/QC, and
  - a reasonable revision allowance.

Use the drawing count, connection count and complexity to derive total_hours.
A typical band is 1.5-8 hours per production drawing depending on complexity.

Return ONLY valid JSON.

{
  "total_hours": 540.0,
  "drawings": 120,
  "connections": 450,
  "complexity": "High",
  "complexity_multiplier": 1.35,
  "drawings_seen": 18,
  "confidence": "medium",
  "notes": "Heavy welded moment connections detected"
}
"""


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """
    Extract JSON object from Gemini response.
    """

    if not text:
        raise ValueError("Empty model response")

    cleaned = text.strip()

    # Remove markdown fences
    cleaned = cleaned.replace("```json", "")
    cleaned = cleaned.replace("```", "").strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("No JSON found in response")

    json_blob = cleaned[start:end + 1]

    return json.loads(json_blob)


# ─────────────────────────────────────────────────────────────
# MAIN FUNCTION
# ─────────────────────────────────────────────────────────────

async def extract_quantities(
    *,
    role: str,
    session_id: str,
    file_paths: Iterable[tuple[str, str]],
) -> tuple[dict, str]:

    """
    Extract quantities from drawings using Gemini.

    Returns:
        (data, engine_label)
    """

    if not file_paths:
        raise ValueError(
            "Upload at least one drawing to run AI estimation."
        )

    if role == "fabricator":
        system_prompt = FABRICATOR_EXTRACT_PROMPT

    elif role == "detailer":
        system_prompt = DETAILER_EXTRACT_PROMPT

    else:
        raise ValueError(
            f"AI estimation only supports detailer or fabricator (got '{role}')"
        )

    user_prompt = """
Analyze all uploaded drawings/files.

Return ONLY the JSON object requested in the system prompt.

No markdown.
No explanation.
No extra text.
"""

    last_err: Exception | None = None
    client = _get_client()  # service-account or API-key aware; created on demand

    for model_name in MODEL_CHAIN:

        try:
            logger.info(
                "AI estimate extraction · tier=%s · session=%s",
                model_name,
                session_id,
            )

            uploaded_files = []

            # Upload files to Gemini
            for file_path, mime_type in file_paths:

                try:
                    uploaded_file = client.files.upload(
                        file=file_path,
                    )

                    uploaded_files.append(uploaded_file)

                except Exception as file_error:
                    logger.warning(
                        "File upload failed for %s: %s",
                        file_path,
                        file_error,
                    )

            # Build content
            contents = [
                system_prompt,
                user_prompt,
            ]

            contents.extend(uploaded_files)

            # Generate response
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
            )

            response_text = response.text.strip()

            data = _extract_json(response_text)

            return data, engine_label(model_name)

        except Exception as e:
            last_err = e

            logger.warning(
                "AI extract tier %s failed: %s",
                model_name,
                e,
            )

            continue

    raise RuntimeError(
        f"STRUCTMIND CORE could not extract quantities. Last error: {last_err}"
    )