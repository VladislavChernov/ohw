"""Bridge to the deterministic Dz2 normalizer and prompt templates.

No normalization or prompt logic is duplicated here: every call goes to
``ai_testgen``. This module only adapts its results to the models of the lab.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from ai_testgen.handlers import get_handler
from ai_testgen.input_doc import RequirementDoc
from ai_testgen.models import ArtifactType
from ai_testgen.normalizer import (
    NormalizationError,
    NormalizationResult,
    NormalizerSettings,
    NormalizerSettingsError,
    decode_bytes,
    load_settings,
    normalize_text,
)
from ai_testgen.presets import PresetError, load_presets, resolve_presets_dir, select_extras
from ai_testgen.prompt import PromptError, resolve_prompt_dir

from .models import NormalizedInput, PromptBundle

SUPPORTED_EXTENSIONS = frozenset({".md", ".txt"})


class BridgeError(Exception):
    """Input, normalizer or prompt configuration failed (exit code 2)."""


def load_normalized(
    source: str | Path,
    *,
    url: str | None = None,
    settings: NormalizerSettings | None = None,
    normalizer_config: Path | None = None,
) -> NormalizedInput:
    """Decode and normalize one input file byte-for-byte deterministically."""
    path = Path(source).expanduser()
    if not path.is_file():
        raise BridgeError(f"input file not found: {path}")

    if settings is None:
        try:
            settings = load_settings(normalizer_config)
        except NormalizerSettingsError as exc:
            raise BridgeError(str(exc)) from exc

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise BridgeError(f"cannot read input {path}: {exc}") from exc
    if not data:
        raise BridgeError(f"input file is empty: {path}")

    try:
        raw_text, encoding = decode_bytes(data, settings)
        result = normalize_text(raw_text, url, encoding=encoding, settings=settings)
    except NormalizationError as exc:
        raise BridgeError(f"{path.name}: {exc}") from exc

    br_ids = tuple(dict.fromkeys(item.id for item in result.requirements))
    return NormalizedInput(
        source=path,
        source_sha256=hashlib.sha256(data).hexdigest(),
        encoding=encoding,
        text=result.text,
        text_sha256=hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
        title=result.title,
        url=result.url,
        br_ids=br_ids,
        settings=settings,
        warnings=tuple(result.warnings),
    )


def is_idempotent(normalized: NormalizedInput) -> bool:
    """``normalize_text`` applied to canonical text must change nothing."""
    again = normalize_text(
        normalized.text,
        normalized.url,
        encoding="utf-8",
        settings=normalized.settings,
    )
    return bool(again.text == normalized.text)


def prompt_sha256(user_prompt: str) -> str:
    return hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()


def build_prompt(
    normalized: NormalizedInput,
    artifact_type: ArtifactType,
    count: int,
    *,
    presets: Sequence[str] = (),
    prompt_dir: Path | None = None,
    presets_dir: Path | None = None,
    testcases: str | None = None,
) -> PromptBundle:
    """Render the system and user prompts from the Dz2 template."""
    try:
        resolved_prompt_dir = resolve_prompt_dir(prompt_dir)
        handler = get_handler(artifact_type, resolved_prompt_dir)
    except PromptError as exc:
        raise BridgeError(str(exc)) from exc

    extras: list[str] = []
    if presets:
        try:
            resolved_presets_dir = resolve_presets_dir(presets_dir)
            if resolved_presets_dir is None:
                raise BridgeError(
                    "presets directory not found: pass --presets-dir or set DZ2_PRESETS_DIR"
                )
            extras = select_extras(load_presets(resolved_presets_dir), list(presets), artifact_type)
        except PresetError as exc:
            raise BridgeError(str(exc)) from exc

    doc = RequirementDoc(
        path=normalized.source,
        content=normalized.text,
        url=normalized.url,
        artifact_type=artifact_type,
    )
    try:
        user = handler.build_user(doc, count, extras=extras, testcases=testcases)
    except PromptError as exc:
        raise BridgeError(str(exc)) from exc

    return PromptBundle(
        artifact_type=artifact_type,
        system=handler.build_system(),
        user=user,
        prompt_sha256=prompt_sha256(user),
        count=count,
        presets=tuple(presets),
    )


def normalizer_snapshot(settings: NormalizerSettings) -> dict[str, object]:
    return {
        "id_prefix": settings.id_prefix,
        "requirements_heading": settings.requirements_heading,
        "url_label": settings.url_label,
        "fallback_title": settings.fallback_title,
        "encodings": list(settings.encodings),
        "paragraph_min_length": settings.paragraph_min_length,
    }


def resolve_input_file(path: str | Path) -> Path:
    """Accept a file or a directory with exactly one supported input."""
    candidate = Path(path).expanduser()
    if candidate.is_file():
        if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise BridgeError(
                f"unsupported input extension {candidate.suffix!r}; expected .md or .txt"
            )
        return candidate
    if not candidate.is_dir():
        raise BridgeError(f"input path not found: {candidate}")

    files = sorted(
        item
        for item in candidate.iterdir()
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not files:
        raise BridgeError(f"no .md/.txt requirement files found in: {candidate}")

    stems: dict[str, list[str]] = {}
    for item in files:
        stems.setdefault(item.stem.lower(), []).append(item.name)
    collisions = {stem: names for stem, names in stems.items() if len(names) > 1}
    if collisions:
        details = "; ".join(
            f"'{stem}': {', '.join(sorted(names))}" for stem, names in sorted(collisions.items())
        )
        raise BridgeError(f"name collision in input directory: {details}")

    return files[0]


def load_normalized_from_config(
    path: str | Path,
    *,
    url: str | None = None,
    normalizer_config: Path | None = None,
) -> NormalizedInput:
    return load_normalized(
        resolve_input_file(path),
        url=url,
        normalizer_config=normalizer_config,
    )


def canonical_text(result: NormalizationResult) -> str:
    """Convenience for tests and UI previews."""
    return str(result.text)
