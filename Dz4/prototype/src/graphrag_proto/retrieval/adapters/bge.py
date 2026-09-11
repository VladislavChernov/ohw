"""HTTP-адаптеры bge-сервисов (M3.2: add-real-embeddings-reranker).

`BgeM3ServiceAdapter` (embeddings-service :8004) и `BgeRerankerAdapter`
(reranker-service :8006). Fail-fast: ошибка транспорта/размерности — явная
ошибка, чтобы ось эмбеддингов (L2-04) не ломалась молча. Стиль:
`topology_client.py` (requests, timeouts, X-API-Key из AUTH_API_KEY env).
"""

from __future__ import annotations

import os

import requests

from graphrag_proto.retrieval.adapters.base import Embedder, Reranker


class EmbeddingServiceError(RuntimeError):
    """Ошибка коммуникации с embeddings-service или невалидный ответ."""


class RerankerServiceError(RuntimeError):
    """Ошибка коммуникации с reranker-service или невалидный ответ."""


class BgeM3ServiceAdapter(Embedder):
    """Эмбеддинг через HTTP :8004 (embeddings-service, bge-m3)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "bge-m3",
        dimensions: int = 1024,
        timeout_s: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._dimensions = dimensions
        self._model = model
        self._endpoint = f"{self._base_url}/api/v1/embed"
        self._headers = {"X-API-Key": api_key} if api_key else {}

    @classmethod
    def from_env(cls) -> BgeM3ServiceAdapter:
        api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "")
        return cls(
            base_url=os.environ.get("EMBEDDINGS_URL", "http://embeddings-service:8004"),
            api_key=api_key,
            model=os.environ.get("EMBEDDING_MODEL", "bge-m3"),
            dimensions=int(os.environ.get("EMBEDDING_DIMENSIONS", "1024")),
            timeout_s=float(os.environ.get("EMBEDDINGS_TIMEOUT_S", "5.0")),
        )

    def embed(self, text: str, domain: str = "") -> list[float]:
        try:
            resp = requests.post(
                self._endpoint,
                json={"text": text, "domain": domain},
                headers=self._headers,
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            body: dict[str, object] = resp.json()
        except requests.RequestException as exc:
            raise EmbeddingServiceError(f"embeddings-service недоступен: {exc}") from exc
        except ValueError as exc:
            raise EmbeddingServiceError(f"невалидный JSON от embeddings-service: {exc}") from exc
        vector = body.get("vector")
        if not isinstance(vector, list) or not all(isinstance(x, (int, float)) for x in vector):
            raise EmbeddingServiceError("неожиданный ответ embeddings-service")
        if len(vector) != self._dimensions:
            raise EmbeddingServiceError(
                f"размерность вектора {len(vector)} != EMBEDDING_DIMENSIONS={self._dimensions}"
            )
        return [float(x) for x in vector]


class BgeRerankerAdapter(Reranker):
    """Реранкинг через HTTP :8006 (reranker-service, bge-reranker-base)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "bge-reranker-base",
        timeout_s: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._model = model
        self._endpoint = f"{self._base_url}/api/v1/rerank"
        self._headers = {"X-API-Key": api_key} if api_key else {}

    @classmethod
    def from_env(cls) -> BgeRerankerAdapter:
        api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "")
        return cls(
            base_url=os.environ.get("RERANKER_URL", "http://reranker:8006"),
            api_key=api_key,
            model=os.environ.get("RERANKER_MODEL", "bge-reranker-base"),
            timeout_s=float(os.environ.get("RERANKER_TIMEOUT_S", "5.0")),
        )

    def rerank(self, query: str, chunks: list[dict[str, object]]) -> list[float]:
        try:
            chunk_payload = [{"id": str(c.get("id", "")), "text": str(c.get("text", ""))} for c in chunks]
            resp = requests.post(
                self._endpoint,
                json={"query": query, "chunks": chunk_payload},
                headers=self._headers,
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            body: dict[str, object] = resp.json()
        except requests.RequestException as exc:
            raise RerankerServiceError(f"reranker-service недоступен: {exc}") from exc
        except ValueError as exc:
            raise RerankerServiceError(f"невалидный JSON от reranker-service: {exc}") from exc
        scores = body.get("scores")
        if not isinstance(scores, list) or len(scores) != len(chunks):
            raise RerankerServiceError("неожиданный ответ reranker-service")
        return [float(x) for x in scores]