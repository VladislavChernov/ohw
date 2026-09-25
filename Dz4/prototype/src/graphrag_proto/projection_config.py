"""Operator-facing settings for the offline projection lifecycle."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml


class ProjectionConfigError(ValueError):
    """Invalid offline projection configuration."""


@dataclass(frozen=True)
class ProjectionSettings:
    lease_seconds: int = 300

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ProjectionSettings:
        unknown = set(data) - {"lease_seconds"}
        if unknown:
            raise ProjectionConfigError(f"unknown projection settings: {sorted(unknown)}")
        raw = data.get("lease_seconds", cls.lease_seconds)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ProjectionConfigError("projection.lease_seconds must be a number")
        value = int(raw)
        if value != raw or value < 30:
            raise ProjectionConfigError("projection.lease_seconds must be an integer >= 30")
        return cls(lease_seconds=value)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _topology_settings() -> Mapping[str, Any] | None:
    url = os.environ.get("TOPOLOGY_URL", "").strip()
    if not url:
        return None
    try:
        timeout_s = float(os.environ.get("TOPOLOGY_TIMEOUT_S", "2"))
    except (TypeError, ValueError):
        timeout_s = 2.0
    try:
        import requests

        key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
        response = requests.get(
            f"{url.rstrip('/')}/api/v1/config/projection",
            headers={"X-API-Key": key},
            timeout=max(0.1, timeout_s),
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return None
    if not isinstance(body, Mapping):
        return None
    payload = body.get("projection")
    return payload if isinstance(payload, Mapping) else None


def load_projection_settings(path: str | Path | None = None) -> ProjectionSettings:
    """Load projection policy from topology YAML, Configurator, then env."""
    config_path = path or os.environ.get("PROJECTION_CONFIG_PATH") or os.environ.get(
        "INFRA_TOPOLOGY_PATH", "infra_topology.yaml"
    )
    file_path = Path(config_path)
    settings = ProjectionSettings()
    if file_path.is_file():
        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ProjectionConfigError(f"cannot read projection config {file_path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise ProjectionConfigError(f"projection config {file_path} must be a mapping")
        section = data.get("projection")
        if section is None:
            if any(key in data for key in ("version", "environment", "providers", "endpoints")):
                raise ProjectionConfigError(
                    f"projection section is missing in topology config {file_path}"
                )
            section = data
        if not isinstance(section, Mapping):
            raise ProjectionConfigError("projection section must be a mapping")
        settings = ProjectionSettings.from_mapping(section)

    topology = _topology_settings()
    if topology is not None:
        settings = ProjectionSettings.from_mapping({**settings.as_dict(), **topology})

    raw_env = os.environ.get("PROJECTION_LEASE_SECONDS", "").strip()
    if raw_env:
        try:
            settings = replace(
                settings,
                lease_seconds=ProjectionSettings.from_mapping(
                    {"lease_seconds": int(raw_env)}
                ).lease_seconds,
            )
        except ValueError as exc:
            raise ProjectionConfigError(f"invalid PROJECTION_LEASE_SECONDS: {exc}") from exc
    return settings
