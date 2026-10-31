"""Reusable prompt presets (extra instructions injected into ``{extra}``).

A preset is a small TOML file::

    id = "boundary-values"
    label = "Граничные значения"
    types = ["testcases", "checklist"]
    text = "Add boundary-value scenarios ..."

The ``id`` must match the file stem. ``types`` defaults to every artifact
type. Presets come from Dz7 (``dz7/presets``) and are copied into
``dz2/presets`` so the CLI, the UI and Docker all read the same files.
"""

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ai_testgen.models import ArtifactType

PRESETS_DIR_NAME = "presets"
PRESETS_DIR_ENV = "DZ2_PRESETS_DIR"
DEFAULT_PRESETS_DIR = Path(__file__).resolve().parent.parent.parent / PRESETS_DIR_NAME
PACKAGE_PRESETS_DIR = Path(__file__).resolve().parent / PRESETS_DIR_NAME


class PresetError(Exception):
    """Preset directory, file or id is missing or malformed."""


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    text: str
    types: tuple[ArtifactType, ...]

    def applies_to(self, artifact_type: ArtifactType) -> bool:
        return artifact_type in self.types


def resolve_presets_dir(explicit: Path | None = None) -> Path | None:
    """Find the directory with ``*.toml`` presets.

    Order: explicit value, ``DZ2_PRESETS_DIR``, ``./presets``, repository
    ``presets``, package ``presets``. Explicit values are strict; when nothing
    is found ``None`` is returned (presets are optional).
    """
    if explicit is not None:
        return _require_dir(explicit, "--presets-dir")

    env_value = os.environ.get(PRESETS_DIR_ENV)
    if env_value:
        return _require_dir(Path(env_value), PRESETS_DIR_ENV)

    for candidate in (
        Path.cwd() / PRESETS_DIR_NAME,
        DEFAULT_PRESETS_DIR,
        PACKAGE_PRESETS_DIR,
    ):
        if candidate.is_dir():
            return candidate
    return None


def _require_dir(path: Path, source: str) -> Path:
    if not path.is_dir():
        raise PresetError(f"presets directory from {source} does not exist: {path}")
    return path


def load_presets(presets_dir: Path) -> dict[str, Preset]:
    """Load every ``*.toml`` preset from ``presets_dir`` keyed by ``id``."""
    if not presets_dir.is_dir():
        raise PresetError(f"presets directory does not exist: {presets_dir}")

    presets: dict[str, Preset] = {}
    for path in sorted(presets_dir.glob("*.toml")):
        preset = _load_preset(path)
        if preset.id in presets:
            raise PresetError(f"duplicate preset id: {preset.id!r}")
        presets[preset.id] = preset

    if not presets:
        raise PresetError(f"no *.toml presets found in: {presets_dir}")
    return presets


def _load_preset(path: Path) -> Preset:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PresetError(f"invalid preset {path}: {exc}") from exc

    values: dict[str, str] = {}
    for key in ("id", "label", "text"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PresetError(f"{path}: '{key}' must be a non-empty string")
        values[key] = value.strip()

    if values["id"] != path.stem:
        raise PresetError(
            f"{path}: 'id' must match the file name ({path.stem!r}), "
            f"got {values['id']!r}"
        )

    raw_types = raw.get("types")
    if raw_types is None:
        preset_types = tuple(ArtifactType)
    else:
        if not isinstance(raw_types, list) or not raw_types:
            raise PresetError(
                f"{path}: 'types' must be a non-empty array of artifact types"
            )
        preset_types = tuple(_artifact_type(str(item), path) for item in raw_types)

    return Preset(
        id=values["id"],
        label=values["label"],
        text=values["text"],
        types=preset_types,
    )


def _artifact_type(value: str, path: Path) -> ArtifactType:
    try:
        return ArtifactType(value.strip().lower())
    except ValueError:
        supported = ", ".join(member.value for member in ArtifactType)
        raise PresetError(
            f"{path}: unknown artifact type {value!r} (supported: {supported})"
        ) from None


def select_extras(
    presets: Mapping[str, Preset],
    preset_ids: Sequence[str],
    artifact_type: ArtifactType,
) -> list[str]:
    """Resolve preset ids into instruction texts for this artifact type."""
    extras: list[str] = []
    for preset_id in preset_ids:
        preset = presets.get(preset_id)
        if preset is None:
            available = ", ".join(sorted(presets)) or "-"
            raise PresetError(
                f"unknown preset: {preset_id!r} (available: {available})"
            )
        if not preset.applies_to(artifact_type):
            supported = ", ".join(member.value for member in preset.types)
            raise PresetError(
                f"preset {preset_id!r} does not apply to {artifact_type.value} "
                f"(types: {supported})"
            )
        extras.append(preset.text)
    return extras
