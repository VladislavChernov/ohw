"""Embeddings Service (:8004): контракт, 422/503, детерминизм, auth — бандл M3.2."""

from __future__ import annotations

from fastapi.testclient import TestClient

from graphrag_proto.embeddings_service.app import create_app
from graphrag_proto.embeddings_service.model import EmbeddingProvider, MockEmbeddingProvider

_AUTH = {"X-API-Key": "changeme"}


class _FailingProvider(EmbeddingProvider):
    """Провайдер, имитирующий недоступность боевой модели."""

    mode = "broken"
    model = "BAAI/none"

    def __init__(self) -> None:
        self.dimensions = 1024

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("боевая модель недоступна")


class _BrokenLoadProvider(EmbeddingProvider):
    """Провайдер, имитирующий сбой загрузки весов (OSError из sentence-transformers)."""

    mode = "broken"
    model = "BAAI/none"

    def __init__(self) -> None:
        self.dimensions = 1024

    def embed(self, text: str) -> list[float]:
        raise OSError("не удалось загрузить веса модели")


def test_health_mock_mode() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=4))
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "embeddings"
    assert body["mode"] == "mock"
    assert body["model"] == "BAAI/bge-m3"
    assert body["dimensions"] == 4


def test_embed_requires_api_key() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        resp = client.post("/api/v1/embed", json={"text": "кэширование данных"})
    assert resp.status_code == 401
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/embed", json={"text": "кэширование данных"}, headers={"X-API-Key": "wrong"}
        )
    assert resp.status_code == 401


def test_embed_vector_dimensions_and_determinism() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/embed", json={"text": "кэширование данных", "domain": "it"}, headers=_AUTH
        ).json()
        second = client.post(
            "/api/v1/embed", json={"text": "кэширование данных", "domain": "it"}, headers=_AUTH
        ).json()
        other = client.post("/api/v1/embed", json={"text": "другая тема"}, headers=_AUTH).json()
    assert first["dimensions"] == 8
    assert len(first["vector"]) == 8
    assert first["vector"] == second["vector"], "одинаковый текст -> одинаковый вектор"
    assert first["vector"] != other["vector"], "разный текст -> разный вектор"


def test_embed_vector_is_l2_normalized() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        vector = client.post(
            "/api/v1/embed", json={"text": "база данных граф"}, headers=_AUTH
        ).json()["vector"]
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_embed_missing_or_blank_text_422() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        assert client.post("/api/v1/embed", json={}, headers=_AUTH).status_code == 422
        assert client.post("/api/v1/embed", json={"text": ""}, headers=_AUTH).status_code == 422
        assert client.post("/api/v1/embed", json={"text": "   "}, headers=_AUTH).status_code == 422


def test_embed_batch_aligned_vectors() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/embed/batch",
            json={"texts": ["первый чанк", "второй чанк"], "domain": "it"},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["dimensions"] == 8
    assert len(payload["vectors"]) == 2
    assert all(len(v) == 8 for v in payload["vectors"])
    assert payload["vectors"][0] != payload["vectors"][1]


def test_embed_batch_empty_or_blank_422() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        assert client.post("/api/v1/embed/batch", json={"texts": []}, headers=_AUTH).status_code == 422
        assert (
            client.post("/api/v1/embed/batch", json={"texts": ["   ", "x"]}, headers=_AUTH).status_code == 422
        )


def test_embed_model_unavailable_503() -> None:
    app = create_app(_FailingProvider())
    with TestClient(app) as client:
        resp = client.post("/api/v1/embed", json={"text": "любой текст"}, headers=_AUTH)
    assert resp.status_code == 503


def test_embed_model_load_failure_503() -> None:
    app = create_app(_BrokenLoadProvider())
    with TestClient(app) as client:
        resp = client.post("/api/v1/embed", json={"text": "любой текст"}, headers=_AUTH)
    assert resp.status_code == 503