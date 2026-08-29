"""Extensible input reading.

The reader registry maps a file extension to a callable ``(Path) -> str``.
Built-in readers cover ``.txt`` and ``.md``; a homework registers more (e.g.
``.pdf`` via pypdf) with ``@register_reader`` without touching the kit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXTENSIONS: tuple[str, ...] = (".txt", ".md")

Reader = Callable[[Path], str]

_READERS: dict[str, Reader] = {}


def register_reader(extension: str) -> Callable[[Reader], Reader]:
    """Register a reader for an extension (e.g. ``.pdf``)."""

    def decorator(reader: Reader) -> Reader:
        normalized = extension.lower()
        if not normalized.startswith("."):
            raise ValueError(f"extension must start with '.', got {extension!r}")
        _READERS[normalized] = reader
        return reader

    return decorator


@register_reader(".txt")
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@register_reader(".md")
def _read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class InputError(Exception):
    """Raised when the input directory cannot be read as expected."""


@dataclass(frozen=True)
class InputFile:
    path: Path
    extension: str
    content: str


def load_input(
    input_dir: Path,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    *,
    recursively: bool = False,
) -> list[InputFile]:
    """Read supported files from ``input_dir`` into ``InputFile`` records.

    Files are ordered by name; name collisions (same stem, different
    extensions) are rejected because the homework typically derives one
    output per stem.
    """
    if not input_dir.is_dir():
        raise InputError(f"input directory does not exist: {input_dir}")

    supported = {ext.lower() for ext in extensions}
    unsupported = sorted(supported - set(_READERS))
    if unsupported:
        supported_known = ", ".join(sorted(_READERS))
        raise InputError(
            f"no reader registered for {', '.join(unsupported)}; "
            f"known readers: {supported_known}"
        )

    candidates = _iter_files(input_dir, recursively=recursively)
    files = [
        p for p in candidates if p.is_file() and p.suffix.lower() in supported
    ]
    if not files:
        raise InputError(f"no supported input files in: {input_dir}")

    collisions = _find_name_collisions(files)
    if collisions:
        details = "; ".join(
            f"'{stem}': {', '.join(sorted(names))}" for stem, names in sorted(collisions.items())
        )
        raise InputError(
            f"name collision in input directory, keep one of each pair "
            f"(delete or rename): {details}"
        )

    docs: list[InputFile] = []
    for path in files:
        reader = _READERS[path.suffix.lower()]
        content = reader(path)
        docs.append(InputFile(path=path, extension=path.suffix.lower(), content=content))
    return docs


def _iter_files(input_dir: Path, *, recursively: bool) -> list[Path]:
    if recursively:
        return sorted(
            p for p in input_dir.rglob("*") if p.is_file()
        )
    return sorted(p for p in input_dir.iterdir() if p.is_file())


def _find_name_collisions(files: list[Path]) -> dict[str, list[str]]:
    by_stem: dict[str, list[str]] = {}
    for path in files:
        by_stem.setdefault(path.stem.lower(), []).append(path.name)
    return {stem: names for stem, names in by_stem.items() if len(names) > 1}