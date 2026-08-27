"""Render a model reply into a markdown output document."""

from datetime import UTC, datetime


def render(text: str, source_name: str) -> str:
    """Wrap a model reply in a document with a header and a generated timestamp."""
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    body = text.strip()
    return (
        f"# Ответ модели — {source_name}\n\n"
        f"<!-- сгенерировано {now} -->\n\n"
        f"{body}\n"
    )