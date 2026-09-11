"""Провайдер оси эмбеддингов Embeddings Service (:8004) — add-real-embeddings-reranker.

Real-режим: lazy-загрузка `SentenceTransformer("BAAI/bge-m3")` на устройстве
`EMBEDDINGS_DEVICE` (cuda при доступности, иначе cpu); encode с L2-нормализацией
и размерностью `EMBEDDING_DIMENSIONS` (по умолчанию 1024), усечение `EMBEDDING_MAX_TOKENS`.
Mock-режим (`EMBEDDINGS_MOCK=true`): детерминированный нормализованный вектор той же
размерности — без ML-зависимостей (автономные тесты, запуск без GPU — L4-01).

ML-зависимости импортируются lazy: модуль корректен и без установленного
sentence-transformers, поэтому готов к тестам и базовому стеку.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = sum(v * v for v in vector) ** 0.5
    if not norm:
        return vector
    return [v / norm for v in vector]


def _mock_vector(text: str, dimensions: int) -> list[float]:
    """Псевдо-вектор размерности bge-m3 (mock): стабилен по тексту, L2-нормализован."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dimensions:
        values += [(b - 127.5) / 127.5 for b in digest]
        digest = hashlib.sha256(digest).digest()
    return _l2_normalize(values[:dimensions])


def _resolve_model(name: str) -> str:
    if name in {"bge-m3", "BAAI/bge-m3"}:
        return "BAAI/bge-m3"
    return name


class EmbeddingProvider:
    """Базовый контракт провайдера (инъекция в тестах вместо env-провайдера)."""

    mode: str = "stub"
    model: str = ""
    dimensions = 0

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class MockEmbeddingProvider(EmbeddingProvider):
    """Детерминированный эмбеддер нужной размерности без ML-зависимостей (mock)."""

    mode = "mock"

    def __init__(self, model: str, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("EMBEDDING_DIMENSIONS должно быть положительным")
        self.model = _resolve_model(model)
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("пустой текст")
        return _mock_vector(text, self.dimensions)


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Реальный bge-m3 через sentence-transformers (lazy-загрузка, fail-fast)."""

    mode = "sentence-transformer"

    def __init__(self, model: str, device: str, dimensions: int, max_tokens: int) -> None:
        self.model = _resolve_model(model)
        self._device = device or "cpu"
        self.dimensions = dimensions
        self._max_tokens = max_tokens
        self._model_cache: Any | None = None

    def _model(self) -> Any:
        if self._model_cache is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers не установлен: соберите образ "
                    "Dockerfile.embeddings или включите EMBEDDINGS_MOCK=true"
                ) from exc
            self._model_cache = SentenceTransformer(self.model, device=self._device)
            if self._max_tokens:
                model = self._model_cache
                model.max_seq_length = min(int(model.max_seq_length), self._max_tokens)
        return self._model_cache

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("пустой текст")
        vector = self._model().encode([text], normalize_embeddings=True)[0]
        values = [float(x) for x in vector]
        if len(values) != self.dimensions:
            raise RuntimeError(
                f"размерность bge-m3 {len(values)} != EMBEDDING_DIMENSIONS={self.dimensions}"
            )
        return values


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def provider_from_env() -> EmbeddingProvider:
    """Сборка провайдера из env: mock-режим (`EMBEDDINGS_MOCK`) или real bge-m3."""
    model = _resolve_model(os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3"))
    dimensions = int(os.environ.get("EMBEDDING_DIMENSIONS", "1024"))
    if os.environ.get("EMBEDDINGS_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return MockEmbeddingProvider(model=model, dimensions=dimensions)
    device = os.environ.get("EMBEDDINGS_DEVICE", "") or ("cuda" if _cuda_available() else "cpu")
    if device == "cuda" and not _cuda_available():
        device = "cpu"
    max_tokens = int(os.environ.get("EMBEDDING_MAX_TOKENS", "8192"))
    return SentenceTransformerEmbeddingProvider(model, device, dimensions, max_tokens)