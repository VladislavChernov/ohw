from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml


def load_namespaces(path: Path) -> dict[str, object]:
    """Дефолты runtime config из `infra/config/namespaces.yaml` (docs/04 §5)."""
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def default_active_profile(namespaces: Mapping[str, object], fallback: str) -> str:
    domain = namespaces.get("domain")
    if isinstance(domain, Mapping):
        active = domain.get("active_profile")
        if isinstance(active, str) and active:
            return active
    return fallback