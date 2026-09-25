from __future__ import annotations

from pathlib import Path

import pytest

from graphrag_proto.redis_config import RedisConfigError, RedisSettings, load_redis_settings


def test_load_redis_settings_from_topology_file(tmp_path: Path) -> None:
    path = tmp_path / "infra_topology.yaml"
    path.write_text(
        """
redis:
  url: redis://config:6379/2
  socket_timeout_s: 3.5
  socket_connect_timeout_s: 4.5
  max_connections: 12
  retry_on_timeout: true
  health_check_interval_s: 15
  read_block_ms: 750
""".strip()
        + "\n",
        encoding="utf-8",
    )

    settings = load_redis_settings(path)

    assert settings == RedisSettings(
        url="redis://config:6379/2",
        socket_timeout_s=3.5,
        socket_connect_timeout_s=4.5,
        max_connections=12,
        retry_on_timeout=True,
        health_check_interval_s=15,
        read_block_ms=750,
    )


def test_redis_settings_env_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "redis.yaml"
    path.write_text(
        "redis:\n  url: redis://file:6379/0\n  max_connections: 12\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QUERY_REDIS_URL", "redis://env:6379/1")
    monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "7")
    monkeypatch.setenv("REDIS_RETRY_ON_TIMEOUT", "yes")
    monkeypatch.setenv("REDIS_READ_BLOCK_MS", "250")

    settings = load_redis_settings(path)

    assert settings.url == "redis://env:6379/1"
    assert settings.max_connections == 7
    assert settings.retry_on_timeout is True
    assert settings.read_block_ms == 250


def test_redis_settings_reject_unknown_field(tmp_path: Path) -> None:
    path = tmp_path / "redis.yaml"
    path.write_text("redis:\n  max_connections: 8\n  magic: true\n", encoding="utf-8")

    with pytest.raises(RedisConfigError, match="unknown"):
        load_redis_settings(path)


def test_redis_settings_reject_malformed_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "redis.yaml"
    path.write_text("redis:\n  url: redis://file:6379/0\n", encoding="utf-8")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT_S", "bad")

    with pytest.raises(RedisConfigError, match="REDIS_SOCKET_TIMEOUT_S"):
        load_redis_settings(path)


def test_redis_settings_client_kwargs_are_derived() -> None:
    kwargs = RedisSettings(max_connections=9, socket_timeout_s=1.25).client_kwargs(
        decode_responses=True
    )

    assert kwargs["max_connections"] == 9
    assert kwargs["socket_timeout"] == 1.25
    assert kwargs["socket_connect_timeout"] == 2.0
    assert kwargs["retry_on_timeout"] is False
    assert kwargs["decode_responses"] is True


def test_topology_configurator_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    path = tmp_path / "infra_topology.yaml"
    path.write_text(
        "redis:\n  url: redis://file:6379/0\n  max_connections: 12\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TOPOLOGY_URL", "http://topology:8005")
    monkeypatch.setenv("AUTH_API_KEY", "test-key")

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"redis": {"max_connections": 6, "read_block_ms": 500}}

    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(requests, "get", fake_get)

    settings = load_redis_settings(path)

    assert captured["url"] == "http://topology:8005/api/v1/config/redis"
    assert captured["headers"] == {"X-API-Key": "test-key"}
    assert settings.max_connections == 6
    assert settings.read_block_ms == 500
    assert settings.url == "redis://file:6379/0"
