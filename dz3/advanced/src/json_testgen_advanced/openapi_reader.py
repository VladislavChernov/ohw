"""Project-side readers registered into the shared ``ohw_kit.io`` registry.

OpenAPI contracts come in ``.json`` / ``.yaml`` / ``.yml``; the kit only
ships ``.txt`` / ``.md``, so this homework registers the missing readers
without touching the kit.
"""

from __future__ import annotations

from pathlib import Path

from ohw_kit.io import register_reader


@register_reader(".json")
def _read_json(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@register_reader(".yaml")
def _read_yaml(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@register_reader(".yml")
def _read_yml(path: Path) -> str:
    return path.read_text(encoding="utf-8")
