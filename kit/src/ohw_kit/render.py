"""Optional helpers for turning a model reply into a Markdown document."""

from __future__ import annotations

from datetime import UTC, datetime


def render_markdown(text: str, source_name: str, title: str | None = None) -> str:
    """Wrap a model reply in a Markdown document with a generated timestamp."""
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    body = text.strip()
    heading = title if title is not None else f"Ответ модели — {source_name}"
    return (
        f"# {heading}\n\n"
        f"<!-- сгенерировано {now} -->\n\n"
        f"{body}\n"
    )