"""Embeddings Service (:8004): контракт, 422/503, детерминизм — бандл M3.2."""

from __future__ import annotations

from fastapi.testclient import TestClient

from graphrag_proto.embeddings_service.app import create_app
from graphrag_proto.embeddings_service.model import EmbeddingProvider, MockEmbeddingProvider


class _FailingProvider(EmbeddingProvider):
    """Провайдер, имитирующий недоступность боевой модели."""

    mode = "broken"
    model = "BAAI/none"

    def __init__(self) -> None:
        self.dimensions = 1024

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("боевая модель недоступна")


def test_health_mock_mode() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=4))
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "embeddings"
    assert body["mode"] == "mock"
    assert body["model"] == "BAAI/bge-m3"
    assert body["dimensions"] == 4


def test_embed_vector_dimensions_and_determinism() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        first = client.post("/api/v1/embed", json={"text": "кэширование данных", "domain": "it"}).json()
        second = client.post("/api/v1/embed", json={"text": "кэширование данных", "domain": "it"}).json()
        other = client.post("/api/v1/embed", json={"text": "другая тема"}).json()
    assert first["dimensions"] == 8
    assert len(first["vector"]) == 8
    assert first["vector"] == second["vector"], "одинаковый текст -> одинаковый вектор"
    assert first["vector"] != other["vector"], "разный текст -> разный вектор"


def test_embed_vector_is_l2_normalized() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        vector = client.post("/api/v1/embed", json={"text": "база данных граф"}).json()["vector"]
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_embed_missing_or_blank_text_422() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        assert client.post("/api/v1/embed", json={}).status_code == 422
        assert client.post("/api/v1/embed", json={"text": ""}).status_code == 422
        assert client.post("/api/v1/embed", json={"text": "   "}).status_code == 422


def test_embed_batch_aligned_vectors() -> None:
    app = create_app(MockEmbeddingProvider(model="bge-m3", dimensions=8))
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/embed/batch", json={"texts": ["первый чанк", "второй чанк"], "domain": "it"}
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
        assert client.post("/api/v1/embed/batch", json={"texts": []}).status_code == 422
        assert client.post("/api/v1/embed/batch", json={"texts": ["   ", "x"]}).status_code == 422


def test_embed_model_unavailable_503() -> None:
    app = create_app(_FailingProvider())
    with TestClient(app) as client:
        resp = client.post("/api/v1/embed", json={"text": "любой текст"})
    assert resp.status_code == 503