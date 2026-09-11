"""Провайдер скоринга Reranker Service (:8006) — add-real-embeddings-reranker.

Real-режим: lazy-загрузка `CrossEncoder("BAAI/bge-reranker-base")` (CPU).
Mock-режим (`RERANKER_MOCK=true`): детерминированный скор по пересечению
лексических токенов (0..1) — без ML-зависимостей; порядок входных чанков
сохраняется (скоры выровнены по индексу).

ML-зависимости импортируются lazy: модуль корректен без sentence-transformers.
"""

from __future__ import annotations

import os
from typing import Any


def _resolve_model(name: str) -> str:
    if name in {"bge-reranker-base", "BAAI/bge-reranker-base"}:
        return "BAAI/bge-reranker-base"
    return name


def _mock_score(query: str, text: str) -> float:
    q_tokens = set(query.lower().split())
    if not q_tokens:
        return 0.0
    overlap = len(q_tokens & set(text.lower().split()))
    return round(overlap / len(q_tokens), 4)


class RerankScorer:
    """Базовый контракт скорера (инъекция в тестах вместо env-скорера)."""

    mode: str = "stub"
    model: str = ""

    def score(self, query: str, texts: list[str]) -> list[float]:
        raise NotImplementedError


class MockRerankScorer(RerankScorer):
    """Лексический скор (пересечение токенов) — контур без ML (mock)."""

    mode = "mock"

    def __init__(self, model: str = "") -> None:
        self.model = model

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [_mock_score(query, text) for text in texts]


class CrossEncoderRerankScorer(RerankScorer):
    """Реальный bge-reranker-base через sentence-transformers (lazy, fail-fast)."""

    mode = "cross-encoder"

    def __init__(self, model: str, device: str = "cpu") -> None:
        self.model = model
        self._device = device or "cpu"
        self._model_cache: Any | None = None

    def _model(self) -> Any:
        if self._model_cache is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers не установлен: соберите образ "
                    "Dockerfile.reranker или включите RERANKER_MOCK=true"
                ) from exc
            self._model_cache = CrossEncoder(self.model, device=self._device)
        return self._model_cache

    def score(self, query: str, texts: list[str]) -> list[float]:
        pairs = [[query, text] for text in texts]
        try:
            scores = self._model().predict(pairs)
        except RuntimeError as exc:
            raise RuntimeError(f"ошибка инференса bge-reranker: {exc}") from exc
        return [float(x) for x in scores]


def scorer_from_env() -> RerankScorer:
    """Сборка скорера из env: mock-режим (`RERANKER_MOCK`) или real cross-encoder."""
    model = _resolve_model(os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-base"))
    if os.environ.get("RERANKER_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return MockRerankScorer(model=model)
    device = os.environ.get("RERANKER_DEVICE", "cpu")
    return CrossEncoderRerankScorer(model=model, device=device)