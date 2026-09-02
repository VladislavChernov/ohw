"""Prompt template loader for the advanced generator."""

from __future__ import annotations

from pathlib import Path


def load_template(path: str | Path) -> str:
    """Read the prompt template text from a file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Промпт-шаблон не найден: {p}")
    return p.read_text(encoding="utf-8")
