from __future__ import annotations

from pathlib import Path

import pytest

from graphrag_proto.projection_config import (
    ProjectionConfigError,
    ProjectionSettings,
    load_projection_settings,
)


def test_load_projection_settings_from_topology_file(tmp_path: Path) -> None:
    path = tmp_path / "infra_topology.yaml"
    path.write_text("projection:\n  lease_seconds: 120\n", encoding="utf-8")

    assert load_projection_settings(path) == ProjectionSettings(lease_seconds=120)


def test_projection_settings_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "infra_topology.yaml"
    path.write_text("projection:\n  lease_seconds: 120\n", encoding="utf-8")
    monkeypatch.setenv("PROJECTION_LEASE_SECONDS", "600")

    assert load_projection_settings(path).lease_seconds == 600


def test_projection_settings_reject_invalid_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "infra_topology.yaml"
    path.write_text("projection:\n  lease_seconds: 5\n", encoding="utf-8")

    with pytest.raises(ProjectionConfigError):
        load_projection_settings(path)

    path.write_text("projection:\n  lease_seconds: 120\n", encoding="utf-8")
    monkeypatch.setenv("PROJECTION_LEASE_SECONDS", "not-a-number")
    with pytest.raises(ProjectionConfigError):
        load_projection_settings(path)


def test_projection_settings_from_configurator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    path = tmp_path / "infra_topology.yaml"
    path.write_text("projection:\n  lease_seconds: 120\n", encoding="utf-8")
    monkeypatch.setenv("TOPOLOGY_URL", "http://topology:8005")
    monkeypatch.setenv("AUTH_API_KEY", "test-key")

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"projection": {"lease_seconds": 240}}

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: _Response())

    assert load_projection_settings(path).lease_seconds == 240
