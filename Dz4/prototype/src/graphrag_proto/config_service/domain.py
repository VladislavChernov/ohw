from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

MANDATORY_SECTIONS = (
    "profile",
    "ontology",
    "extraction",
    "validation",
    "canonicalization",
    "chunking",
    "context_assembly",
)


class DomainProfileError(ValueError):
    """Ошибка формата/содержимого Domain Profile."""


def load_profile_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise DomainProfileError(f"{path.name}: профиль должен быть YAML-маппингом")
    errors = validate_profile(data)
    if errors:
        raise DomainProfileError(f"{path.name}: {'; '.join(errors)}")
    return data


def validate_profile(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for section in MANDATORY_SECTIONS:
        if section not in data:
            errors.append(f"отсутствует секция '{section}'")
    ontology = data.get("ontology")
    if isinstance(ontology, dict):
        for key in ("node_types", "edge_types"):
            if not isinstance(ontology.get(key), list):
                errors.append(f"ontology.{key}: должен быть список")
    return errors


def domain_from_profile(profile: dict[str, Any], default: str) -> str:
    name = profile.get("profile", {}).get("name")
    return name if isinstance(name, str) and name else default


def domain_from_filename(path: Path) -> str | None:
    stem = path.stem
    if stem.startswith("domain_profile."):
        return stem.removeprefix("domain_profile.")
    return None