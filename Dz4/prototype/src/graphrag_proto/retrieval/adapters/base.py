"""ABC-контракты адаптерного слоя (docs/adapters_specification.md §2, L1-02).

Расширение M2 (зафиксировано в docs/adapters_specification.md §2.6):
- `transaction()` на оба хранилища — атомарная запись узлов/рёбер + эмбеддингов (L2-04);
- `GraphStoreProvider.list_chunk_ids_of_source` — обход CONTAINS для soft-delete (L2-05);
- `VectorStoreProvider.delete_vectors` — снятие чанков с поиска при soft-delete (L2-05).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from typing import Any

GraphTx = AbstractContextManager["GraphStoreProvider"]
VectorTx = AbstractContextManager["VectorStoreProvider"]


class Embedder(ABC):
    """Эмбеддинг текста (M2: DeterministicEmbedder, без GPU — L4-01)."""

    @abstractmethod
    def embed(self, text: str, domain: str = "") -> list[float]:
        """Вектор фиксированной размерности для text."""


class Reranker(ABC):
    """Переранжирование чанков."""

    @abstractmethod
    def rerank(self, query: str, chunks: list[dict[str, Any]]) -> list[float]:
        """Скоры, выровненные по индексу входного списка chunks."""


class LLMInference(ABC):
    """Инференс LLM. Возвращает стрим дельт текста (для SSE token-событий)."""

    @abstractmethod
    def generate(self, prompt: str, system: str = "", stream: bool = True) -> Iterator[str]:
        """Итератор текстовых дельт; stream=False — один дельта с полным ответом."""


class GraphStoreProvider(ABC):
    """Графовая ось (ADR-013): логические связи, обход, Cypher."""

    @abstractmethod
    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Выполнение графового Cypher-запроса (обход связей, расширение)."""

    @abstractmethod
    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        """Массовая вставка/обновление узлов (MERGE по node_id)."""

    @abstractmethod
    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        """Массовая вставка/обновление рёбер."""

    @abstractmethod
    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Получение узла по node_id."""

    @abstractmethod
    def delete_node(self, node_id: str) -> bool:
        """Удаление узла по node_id (вместе с инцидентными рёбрами)."""

    @abstractmethod
    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        """Чанки источника по связи CONTAINS (soft-delete, L2-05)."""

    def transaction(self) -> GraphTx:
        """Атомарная запись пачки изменений (M2, L2-04). По умолчанию — no-op."""
        return nullcontext(self)


class VectorStoreProvider(ABC):
    """Векторная ось (ADR-013): косинусный поиск по единицам чанков."""

    @abstractmethod
    def vector_search(self, embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        """Косинусный поиск топ-K ближайших чанков."""

    @abstractmethod
    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        """Запись/обновление эмбеддингов (chunk_id -> vector + metadata)."""

    @abstractmethod
    def delete_vectors(self, chunk_ids: list[str]) -> None:
        """Снятие чанков с поиска (soft-delete, L2-05)."""

    def transaction(self) -> VectorTx:
        """Атомарная запись пачки эмбеддингов (M2, L2-04). По умолчанию — no-op."""
        return nullcontext(self)