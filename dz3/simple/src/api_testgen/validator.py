"""Coverage validation of LLM-generated test code."""

from __future__ import annotations


def find_missing(code: str, required: list[str]) -> list[str]:
    """Return required markers that are absent from the generated code.

    Markers are case-insensitive substrings (typically HTTP verbs such as
    "GET" or "POST"). Order follows the `required` list.
    """
    lowered = code.lower()
    return [marker for marker in required if marker.lower() not in lowered]


def build_feedback(missing: list[str]) -> str:
    """Build a follow-up instruction asking the model to fix its coverage."""
    return (
        "Your previous answer is incomplete: it does not cover "
        + ", ".join(missing)
        + ". Regenerate the FULL pytest test file covering ALL of them "
        "(one or more tests per item). Respond with the complete file only."
    )
