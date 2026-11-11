"""Guard test: documentation must be valid UTF-8 without broken characters.

The project ships Russian Markdown, and broken encoding (``U+FFFD`` or stray
control characters) is easy to introduce on Windows and hard to notice in a
review. The test scans every tracked-like ``.md`` file in the project.

Excluded directories: ``.venv``, ``output`` and tool caches.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".venv", "output", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def _markdown_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for path in ROOT.rglob("*.md"):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _broken_characters(text: str) -> list[tuple[int, int, str]]:
    broken: list[tuple[int, int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for column, char in enumerate(line, start=1):
            if char == "\ufffd" or (ord(char) < 32 and char not in "\n\r\t"):
                broken.append((lineno, column, char))
    return broken


@pytest.mark.parametrize(
    "path",
    _markdown_files(),
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_markdown_is_valid_utf8_without_broken_characters(
    path: pathlib.Path,
) -> None:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"{path.relative_to(ROOT)} is not valid UTF-8: {exc}")

    broken = _broken_characters(text)
    assert not broken, (
        f"{path.relative_to(ROOT)} contains broken characters "
        f"(line, column, codepoint): {broken[:5]}"
    )


def test_markdown_set_is_not_empty() -> None:
    """A broken glob must not silently turn the guard into a no-op."""
    assert len(_markdown_files()) >= 10
