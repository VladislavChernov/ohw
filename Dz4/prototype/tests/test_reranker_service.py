"""Reranker Service (:8006): контракт, выравнивание скоров, 422/503, auth — бандл M3.2."""

from __future__ import annotations

from fastapi.testclient import TestClient

from graphrag_proto.reranker_service.app import create_app
from graphrag_proto.reranker_service.model import MockRerankScorer, RerankScorer

_AUTH = {"X-API-Key": "changeme"}


class _FailingScorer(RerankScorer):
    """Скорер, имитирующий недоступность боевой модели."""

    mode = "broken"
    model = "BAAI/none"

    def score(self, query: str, texts: list[str]) -> list[float]:
        raise RuntimeError("боевая модель недоступна")


class _BrokenLoadScorer(_FailingScorer):
    """Скорер, имитирующий сбой загрузки весов (OSError из sentence-transformers)."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        raise OSError("не удалось загрузить веса модели")


class _FixedScorer(RerankScorer):
    """Фиксированные скоры — проверка выравнивания по индексу входных чанков."""

    mode = "fixed"

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores
        self.model = "stub"

    def score(self, query: str, texts: list[str]) -> list[float]:
        assert len(texts) == len(self._scores)
        return list(self._scores)


def test_health_mock_mode() -> None:
    app = create_app(MockRerankScorer())
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "reranker"
    assert body["mode"] == "mock"


def test_rerank_requires_api_key() -> None:
    app = create_app(MockRerankScorer())
    with TestClient(app) as client:
        resp = client.post("/api/v1/rerank", json={"query": "q", "chunks": [{"text": "x"}]})
    assert resp.status_code == 401


def test_rerank_scores_aligned_with_chunks() -> None:
    app = create_app(_FixedScorer([1.0, 0.5, 0.0]))
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/rerank",
            headers=_AUTH,
            json={
                "query": "базы данных",
                "chunks": [
                    {"id": "chk:a", "text": "про базы"},
                    {"id": "chk:b", "text": "про граф"},
                    {"id": "chk:c", "text": "про сети"},
                ],
            },
        )
    assert resp.status_code == 200
    assert resp.json()["scores"] == [1.0, 0.5, 0.0]


def test_rerank_mock_lexical_score() -> None:
    scorer = MockRerankScorer()
    scores = scorer.score("база данных граф", ["база данных", "кино и театр"])
    assert scores[0] > scores[1]
    assert 0.0 <= scores[0] <= 1.0


def test_rerank_empty_query_or_chunks_422() -> None:
    app = create_app(MockRerankScorer())
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/rerank", json={"query": "q", "chunks": []}, headers=_AUTH).status_code == 422
        )
        assert (
            client.post(
                "/api/v1/rerank", json={"query": "  ", "chunks": [{"text": "x"}]}, headers=_AUTH
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/rerank", json={"query": "q", "chunks": [{"text": "  "}]}, headers=_AUTH
            ).status_code
            == 422
        )


def test_rerank_model_unavailable_503() -> None:
    app = create_app(_FailingScorer())
    with TestClient(app) as client:
        resp = client.post("/api/v1/rerank", json={"query": "q", "chunks": [{"text": "x"}]}, headers=_AUTH)
    assert resp.status_code == 503


def test_rerank_model_load_failure_503() -> None:
    app = create_app(_BrokenLoadScorer())
    with TestClient(app) as client:
        resp = client.post("/api/v1/rerank", json={"query": "q", "chunks": [{"text": "x"}]}, headers=_AUTH)
    assert resp.status_code == 503