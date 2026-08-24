import re
from dataclasses import dataclass
from pathlib import Path

from ai_testgen.models import ArtifactType

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt"})

_URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_FRONT_MATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_KNOWN_FRONT_MATTER_KEYS = frozenset({"type"})
_ARTIFACT_TYPES = {member.value: member for member in ArtifactType}


class InputError(Exception):
    pass


@dataclass(frozen=True)
class RequirementDoc:
    path: Path
    content: str
    url: str | None
    artifact_type: ArtifactType = ArtifactType.TESTCASES


def extract_url(content: str) -> str | None:
    match = _URL_PATTERN.search(content)
    if match is None:
        return None
    return match.group(0).rstrip(".,;:")


def split_front_matter(content: str) -> tuple[ArtifactType, str]:
    match = _FRONT_MATTER_PATTERN.match(content)
    if match is None:
        return ArtifactType.TESTCASES, content

    artifact_type = ArtifactType.TESTCASES
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise InputError(f"malformed front-matter line (expected 'key: value'): {line!r}")
        key = key.strip().lower()
        if key not in _KNOWN_FRONT_MATTER_KEYS:
            raise InputError(f"unknown front-matter key: {key!r} (supported: type)")
        if key == "type":
            artifact_type = _parse_artifact_type(value.strip())

    return artifact_type, content[match.end() :].lstrip("\n")


def _parse_artifact_type(value: str) -> ArtifactType:
    normalized = value.lower()
    if normalized not in _ARTIFACT_TYPES:
        supported = ", ".join(sorted(_ARTIFACT_TYPES))
        raise InputError(f"unknown artifact type: {value!r} (supported: {supported})")
    return _ARTIFACT_TYPES[normalized]


def collect_input_files(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise InputError(f"input directory does not exist: {input_dir}")

    files = sorted(
        p
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    skipped = sorted(
        p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() not in SUPPORTED_EXTENSIONS
    )
    for path in skipped:
        print(f"warning: skipping unsupported file: {path.name}", flush=True)

    if not files:
        raise InputError(f"no .md/.txt requirement files found in: {input_dir}")

    collisions = _find_name_collisions(files)
    if collisions:
        details = "; ".join(
            f"'{stem}': {', '.join(sorted(names))}" for stem, names in sorted(collisions.items())
        )
        raise InputError(
            f"name collision in input directory, keep only one of each pair (delete or rename): {details}"
        )
    return files


def load_docs(input_dir: Path, url_override: str | None) -> list[RequirementDoc]:
    docs: list[RequirementDoc] = []
    for path in collect_input_files(input_dir):
        content = path.read_text(encoding="utf-8")
        artifact_type, body = split_front_matter(content)
        docs.append(
            RequirementDoc(
                path=path,
                content=body,
                url=url_override if url_override else extract_url(body),
                artifact_type=artifact_type,
            )
        )
    return docs


def _find_name_collisions(files: list[Path]) -> dict[str, list[str]]:
    by_stem: dict[str, list[str]] = {}
    for path in files:
        by_stem.setdefault(path.stem.lower(), []).append(path.name)
    return {stem: names for stem, names in by_stem.items() if len(names) > 1}
