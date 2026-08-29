"""Prompt loader — reads a concrete prompt file to send to the Ollama server."""

from __future__ import annotations

from pathlib import Path


def load_prompt(path: Path) -> str:
    """Read the prompt text from a file."""
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def resolve_prompt_path(prompt_file: str | None, input_dir: Path) -> Path:
    """Resolve the prompt file path: explicit flag or default prompt.txt in input dir."""
    if prompt_file:
        return Path(prompt_file)
    return input_dir / "prompt.txt"
