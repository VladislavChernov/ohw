"""Read request files (.md/.txt) from the input directory."""

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".txt"}


class InputError(Exception):
    pass


@dataclass(frozen=True)
class RequestDoc:
    path: Path
    content: str


def load_docs(input_dir: Path) -> list[RequestDoc]:
    if not input_dir.is_dir():
        raise InputError(f"input directory not found: {input_dir}")
    files = sorted(
        (p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda p: p.name,
    )
    if not files:
        raise InputError(f"no .md/.txt files found in {input_dir}")
    _check_collisions(files)
    return [RequestDoc(path=p, content=p.read_text(encoding="utf-8")) for p in files]


def _check_collisions(files: list[Path]) -> None:
    by_stem: dict[str, list[Path]] = {}
    for path in files:
        by_stem.setdefault(path.stem.lower(), []).append(path)
    for stem, paths in by_stem.items():
        if len(paths) > 1:
            listed = ", ".join(str(p) for p in paths)
            raise InputError(
                f"name collision for '{stem}': {listed} map to the same output file; "
                "remove or rename one of them"
            )