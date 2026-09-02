"""Extract strict JSON from an LLM reply (shared by feedback loops)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = ["JsonReplyError", "ValidationResult", "extract_json"]


class JsonReplyError(ValueError):
    """The model reply does not contain parseable JSON.

    ``message`` is worded so it can be sent back to the model as feedback.
    """


@dataclass
class ValidationResult:
    """Common result shape for LLM-answer validators (ok + feedback issues)."""

    ok: bool
    issues: list[str] = field(default_factory=list)

    @classmethod
    def success(cls) -> ValidationResult:
        return cls(ok=True, issues=[])

    @classmethod
    def failure(cls, issues: list[str]) -> ValidationResult:
        return cls(ok=False, issues=list(issues))


def extract_json(text: str) -> dict | list:
    """Parse the model reply as JSON, tolerating markdown code fences.

    Handles: bare JSON, ```json fences, prose around a fenced block.
    Raises ``JsonReplyError`` with model-directed feedback otherwise.
    """
    body = text.strip()
    if "```" in body:
        chunks = [c.strip() for c in body.split("```") if c.strip()]
        candidates = [c.removeprefix("json").strip() for c in chunks]
    else:
        candidates = [body]

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
        raise JsonReplyError(
            "Ответ должен быть JSON-объектом или массивом, получен скаляр. "
            "Перегенерируй ответ — только JSON, без пояснений."
        )
    raise JsonReplyError(
        "Ответ не является корректным JSON. Перегенерируй ответ — "
        "только валидный JSON, без markdown-ограждений и пояснений."
    )
