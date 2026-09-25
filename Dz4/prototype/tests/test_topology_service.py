"""Topology Orchestrator (:8005): X-API-Key, базовые эндпоинты, PUT/валидация."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from graphrag_proto.topology_service.app import create_app
from graphrag_proto.topology_service.topology import TopologyError

API_KEY = "test-topology-key"

TOPOLOGY = {
    "version": "0.1",
    "environment": "dev",
    "network": {"default_bridge": "ohw_net"},
    "providers": {
        "graph_store": "neo4j",
        "vector_store": "inmemory",
        "embeddings": "deterministic",
        "reranker": "noop",
        "llm": "openai",
    },
    "endpoints": {
        "neo4j": {"bolt": "neo4j://neo4j:7687", "http": "http://neo4j:7474"},
        "llm": {"base": "http://llm:8080"},
    },
    "redis": {
        "url": "redis://valkey:6379/0",
        "socket_timeout_s": 2,
        "socket_connect_timeout_s": 2,
        "max_connections": 32,
        "retry_on_timeout": False,
        "health_check_interval_s": 30,
        "read_block_ms": 1000,
    },
    "projection": {"lease_seconds": 300},
    "startup": {"auto_apply_adapters": True, "require_topology_apply": False},
}


@pytest.fixture()
def topology_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "infra_topology.yaml"
    path.write_text(yaml.safe_dump(TOPOLOGY), encoding="utf-8")
    return path


@pytest.fixture()
def client(topology_yaml: Path, tmp_path: Path) -> TestClient:
    return TestClient(create_app(topology_yaml, tmp_path / "topo.sqlite", API_KEY))


def headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def test_health_without_key(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    assert client.get("/health").json()["status"] == "ok"


def test_auth_required(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    assert client.get("/api/v1/topology").status_code == 401
    assert client.get("/api/v1/config/adapters").status_code == 401
    assert client.put("/api/v1/config/adapters", json={"vector_store": "inmemory"}).status_code == 401
    assert client.get("/api/v1/config/adapters/available").status_code == 401
    assert client.get("/api/v1/config/redis").status_code == 401
    assert client.put("/api/v1/config/redis", json={"max_connections": 8}).status_code == 401
    assert client.get("/api/v1/config/projection").status_code == 401
    assert client.put("/api/v1/config/projection", json={"lease_seconds": 120}).status_code == 401
    assert client.get("/api/v1/topology", headers={"X-API-Key": "wrong"}).status_code == 401


def test_get_adapters_base(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    resp = client.get("/api/v1/config/adapters", headers=headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["revision"] == 0
    assert body["adapters"]["graph_store"] == "neo4j"
    assert body["adapters"]["vector_store"] == "inmemory"


def test_put_switches_adapter_and_bumps_revision(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    resp = client.put("/api/v1/config/adapters", headers=headers(), json={"vector_store": "neo4j"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["revision"] == 1
    assert body["adapters"]["vector_store"] == "neo4j"
    assert body["adapters"]["graph_store"] == "neo4j"

    again = client.get("/api/v1/config/adapters", headers=headers()).json()
    assert again["revision"] == 1
    assert again["adapters"]["vector_store"] == "neo4j"


def test_put_unknown_provider_422_no_revision_bump(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    resp = client.put("/api/v1/config/adapters", headers=headers(), json={"llm": "mistral"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "mistral" in detail
    assert client.get("/api/v1/config/adapters", headers=headers()).json()["revision"] == 0


def test_put_unknown_slot_422(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    assert client.put("/api/v1/config/adapters", headers=headers(), json={"cache": "valkey"}).status_code == 422


def test_put_idempotent_no_revision_bump(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    first = client.put("/api/v1/config/adapters", headers=headers(), json={"vector_store": "neo4j"}).json()
    second = client.put("/api/v1/config/adapters", headers=headers(), json={"vector_store": "neo4j"}).json()
    assert first["revision"] == 1
    assert second["revision"] == 1


def test_put_auth_wrong_key_preserves_revision(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    assert client.put("/api/v1/config/adapters", headers={"X-API-Key": "nope"}, json={"vector_store": "neo4j"}).status_code == 401
    assert client.get("/api/v1/config/adapters", headers=headers()).json()["revision"] == 0


def test_redis_settings_get_and_put_persist_override(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "redis.sqlite", API_KEY))
    initial = client.get("/api/v1/config/redis", headers=headers()).json()
    assert initial["redis"]["max_connections"] == 32
    assert initial["redis"]["read_block_ms"] == 1000

    response = client.put(
        "/api/v1/config/redis",
        headers=headers(),
        json={"max_connections": 8, "socket_timeout_s": 1.5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 1
    assert body["redis"]["max_connections"] == 8
    assert body["redis"]["socket_timeout_s"] == 1.5
    assert client.get("/api/v1/config/redis", headers=headers()).json()["redis"]["max_connections"] == 8


def test_redis_settings_reject_unknown_and_invalid_values(
    topology_yaml: Path, tmp_path: Path
) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "redis-invalid.sqlite", API_KEY))
    unknown = client.put(
        "/api/v1/config/redis", headers=headers(), json={"pool_magic": 1}
    )
    invalid = client.put(
        "/api/v1/config/redis", headers=headers(), json={"max_connections": 0}
    )

    assert unknown.status_code == 422
    assert invalid.status_code == 422
    assert client.get("/api/v1/config/redis", headers=headers()).json()["revision"] == 0


def test_projection_settings_get_and_put_persist_override(
    topology_yaml: Path, tmp_path: Path
) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "projection.sqlite", API_KEY))
    initial = client.get("/api/v1/config/projection", headers=headers()).json()
    assert initial["projection"]["lease_seconds"] == 300

    response = client.put(
        "/api/v1/config/projection",
        headers=headers(),
        json={"lease_seconds": 120},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 1
    assert body["projection"]["lease_seconds"] == 120
    assert client.get("/api/v1/config/projection", headers=headers()).json()["projection"][
        "lease_seconds"
    ] == 120


def test_projection_settings_reject_invalid_lease(
    topology_yaml: Path, tmp_path: Path
) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "projection-invalid.sqlite", API_KEY))
    response = client.put(
        "/api/v1/config/projection", headers=headers(), json={"lease_seconds": 5}
    )

    assert response.status_code == 422
    assert client.get("/api/v1/config/projection", headers=headers()).json()["revision"] == 0


def test_get_available(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    resp = client.get("/api/v1/config/adapters/available", headers=headers())
    assert resp.status_code == 200
    slots = resp.json()["slots"]
    assert slots["graph_store"] == ["neo4j", "inmemory"]
    assert slots["reranker"] == ["noop", "bge_reranker"]
    assert slots["embeddings"] == ["deterministic", "bge_m3_service"]


def test_get_topology(topology_yaml: Path, tmp_path: Path) -> None:
    client = TestClient(create_app(topology_yaml, tmp_path / "t.sqlite", API_KEY))
    resp = client.get("/api/v1/topology", headers=headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["providers"]["graph_store"] == "neo4j"
    assert body["redis"]["max_connections"] == 32
    assert body["projection"]["lease_seconds"] == 300
    assert body["startup"]["auto_apply_adapters"] is True


def test_invalid_topology_yaml_raises_at_startup(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "0.1",
                "environment": "dev",
                "network": {"default_bridge": "ohw_net"},
                "providers": {
                    "graph_store": "postgres",
                    "vector_store": "inmemory",
                    "embeddings": "deterministic",
                    "reranker": "noop",
                    "llm": "openai",
                },
                "endpoints": {"llm": {"base": "http://llm:8080"}},
                "redis": {"url": "redis://valkey:6379/0"},
                "projection": {"lease_seconds": 300},
                "startup": {"auto_apply_adapters": True},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(TopologyError, match="graph_store"):
        create_app(path, tmp_path / "bad.sqlite", API_KEY)