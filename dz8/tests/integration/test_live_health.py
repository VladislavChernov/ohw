"""Live health checks against the stand."""

from __future__ import annotations

import pytest

from llm_degradation.ollama import LabOllamaClient

pytestmark = pytest.mark.integration


def test_health_reports_the_configured_model(live_client: LabOllamaClient) -> None:
    health = live_client.health()
    assert health["ok"] is True
    assert health["digest"]
    assert health["ollama_version"]


def test_version_and_tags(live_client: LabOllamaClient) -> None:
    assert live_client.version()["version"]
    names = live_client.list_models()
    assert names
    assert any(item.split(":", 1)[0] == live_client.model for item in names)


def test_model_show_returns_metadata(live_client: LabOllamaClient) -> None:
    payload = live_client.show()
    assert isinstance(payload.get("details"), dict)
    assert isinstance(payload.get("model_info"), dict)
