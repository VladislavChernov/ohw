"""Runtime Redis/Valkey settings loaded from the installation topology.

The topology YAML is the operator-facing source of truth. Environment variables
are explicit deployment overrides; the dataclass defaults only keep standalone
unit tests and legacy single-process demos working without a topology file.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml


class RedisConfigError(ValueError):
    """Invalid Redis/Valkey runtime configuration."""


@dataclass(frozen=True)
class RedisSettings:
    url: str = "redis://valkey:6379/0"
    socket_timeout_s: float = 2.0
    socket_connect_timeout_s: float = 2.0
    max_connections: int = 32
    retry_on_timeout: bool = False
    health_check_interval_s: int = 30
    read_block_ms: int = 1000

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> RedisSettings:
        allowed = {
            "url",
            "socket_timeout_s",
            "socket_connect_timeout_s",
            "max_connections",
            "retry_on_timeout",
            "health_check_interval_s",
            "read_block_ms",
        }
        unknown = set(data) - allowed
        if unknown:
            raise RedisConfigError(f"unknown Redis settings: {sorted(unknown)}")
        url = data.get("url", cls.url)
        if not isinstance(url, str) or not url.strip():
            raise RedisConfigError("redis.url must be a non-empty string")
        return cls(
            url=url.strip(),
            socket_timeout_s=_positive_float(
                data.get("socket_timeout_s", cls.socket_timeout_s),
                "redis.socket_timeout_s",
            ),
            socket_connect_timeout_s=_positive_float(
                data.get("socket_connect_timeout_s", cls.socket_connect_timeout_s),
                "redis.socket_connect_timeout_s",
            ),
            max_connections=_positive_int(
                data.get("max_connections", cls.max_connections),
                "redis.max_connections",
            ),
            retry_on_timeout=_boolean(
                data.get("retry_on_timeout", cls.retry_on_timeout),
                "redis.retry_on_timeout",
            ),
            health_check_interval_s=_non_negative_int(
                data.get("health_check_interval_s", cls.health_check_interval_s),
                "redis.health_check_interval_s",
            ),
            read_block_ms=_positive_int(
                data.get("read_block_ms", cls.read_block_ms),
                "redis.read_block_ms",
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def client_kwargs(self, *, decode_responses: bool) -> dict[str, Any]:
        return {
            "decode_responses": decode_responses,
            "socket_timeout": self.socket_timeout_s,
            "socket_connect_timeout": self.socket_connect_timeout_s,
            "max_connections": self.max_connections,
            "retry_on_timeout": self.retry_on_timeout,
            "health_check_interval": self.health_check_interval_s,
        }


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RedisConfigError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise RedisConfigError(f"{name} must be > 0")
    return result


def _positive_int(value: Any, name: str) -> int:
    result = _non_negative_int(value, name)
    if result < 1:
        raise RedisConfigError(f"{name} must be >= 1")
    return result


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RedisConfigError(f"{name} must be an integer")
    if value < 0:
        raise RedisConfigError(f"{name} must be >= 0")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise RedisConfigError(f"{name} must be boolean")
    return value


def _env_override(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


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
            f"{url.rstrip('/')}/api/v1/config/redis",
            headers={"X-API-Key": key},
            timeout=max(0.1, timeout_s),
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return None
    if not isinstance(body, Mapping):
        return None
    payload = body.get("redis")
    return payload if isinstance(payload, Mapping) else None


def load_redis_settings(path: str | Path | None = None) -> RedisSettings:
    """Load `redis` from topology YAML, then apply explicit env overrides.

    `REDIS_CONFIG_PATH` may point either at a full topology file or at a file
    containing the `redis` mapping itself. `INFRA_TOPOLOGY_PATH` is retained as
    the container-friendly alias used by the topology service.
    """
    config_path = path or os.environ.get("REDIS_CONFIG_PATH") or os.environ.get(
        "INFRA_TOPOLOGY_PATH", "infra_topology.yaml"
    )
    file_path = Path(config_path)
    settings = RedisSettings()
    if file_path.is_file():
        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RedisConfigError(f"cannot read Redis config {file_path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise RedisConfigError(f"Redis config {file_path} must be a mapping")
        section = data.get("redis")
        if section is None:
            if any(key in data for key in ("version", "environment", "providers", "endpoints")):
                raise RedisConfigError(
                    f"redis section is missing in topology config {file_path}"
                )
            section = data
        if not isinstance(section, Mapping):
            raise RedisConfigError("redis section must be a mapping")
        settings = RedisSettings.from_mapping(section)

    topology = _topology_settings()
    if topology is not None:
        settings = RedisSettings.from_mapping({**settings.as_dict(), **topology})

    overrides: dict[str, Any] = {}
    url = _env_override("QUERY_REDIS_URL") or _env_override("REDIS_URL")
    if url is not None:
        overrides["url"] = url
    for env_name, field_name in (
        ("REDIS_SOCKET_TIMEOUT_S", "socket_timeout_s"),
        ("REDIS_SOCKET_CONNECT_TIMEOUT_S", "socket_connect_timeout_s"),
    ):
        raw = _env_override(env_name)
        if raw is not None:
            try:
                numeric = float(raw)
            except ValueError as exc:
                raise RedisConfigError(f"env {env_name} must be a number") from exc
            overrides[field_name] = _positive_float(numeric, f"env {env_name}")
    for env_name, field_name in (
        ("REDIS_MAX_CONNECTIONS", "max_connections"),
        ("REDIS_HEALTH_CHECK_INTERVAL_S", "health_check_interval_s"),
        ("REDIS_READ_BLOCK_MS", "read_block_ms"),
    ):
        raw = _env_override(env_name)
        if raw is not None:
            parser = _positive_int if field_name != "health_check_interval_s" else _non_negative_int
            try:
                numeric = int(raw)
            except ValueError as exc:
                raise RedisConfigError(f"env {env_name} must be an integer") from exc
            overrides[field_name] = parser(numeric, f"env {env_name}")
    retry = _env_override("REDIS_RETRY_ON_TIMEOUT")
    if retry is not None:
        normalized = retry.lower()
        if normalized not in {"0", "1", "false", "true", "no", "yes", "on", "off"}:
            raise RedisConfigError(f"env REDIS_RETRY_ON_TIMEOUT has invalid value {retry!r}")
        overrides["retry_on_timeout"] = normalized in {"1", "true", "yes", "on"}
    return replace(settings, **overrides) if overrides else settings
