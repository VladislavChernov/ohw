"""Fixtures shared by the live integration tests.

Every integration test is skipped when the stand is not reachable, so the
default ``pytest -m "not integration"`` run never touches the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from llm_degradation.config import LabConfig, load_config
from llm_degradation.ollama import LabOllamaClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def live_config() -> LabConfig:
    return load_config(ROOT / "config.toml", env={})


@pytest.fixture(scope="session")
def live_client(live_config: LabConfig) -> Iterator[LabOllamaClient]:
    client = LabOllamaClient(
        live_config.base_url,
        live_config.model,
        timeout=live_config.timeout,
    )
    health = client.health()
    if not health.get("ok"):
        client.close()
        pytest.skip(f"live Ollama is not available: {health.get('error')}")
    yield client
    client.close()
