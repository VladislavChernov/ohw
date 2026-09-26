"""ABC-контракты адаптерного слоя (docs/adapters_specification.md §2, L1-02).

Расширение M2 (зафиксировано в docs/adapters_specification.md §2.6):
- `transaction()` на оба хранилища — атомарная запись узлов/рёбер + эмбеддингов (L2-04);
- `GraphStoreProvider.list_chunk_ids_of_source` — обход CONTAINS для soft-delete (L2-05);
- `VectorStoreProvider.delete_vectors` — снятие чанков с поиска при soft-delete (L2-05).

Расширение A-2 (ADR-024, `consistency_capability`):
- `consistency_capability()` / `engine_key()` — capability-контракт атомарности COMMIT:
  `"atomic"` ось может писать обе оси пары в одной транзакции движка; `"best_effort"` — дефолт;
- `atomic_batch()` — контекст-координатор для обеих осей атомарной пары (для Neo4j:
  один `session.begin_transaction()`; для InMemory: вложенные transaction()-контексты
  в одном процессе, общий откат при ошибке).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Literal, Protocol

GraphTx = AbstractContextManager["GraphStoreProvider"]
VectorTx = AbstractContextManager["VectorStoreProvider"]


def _expand_direction(direction: str) -> str:
    """Нормализует направление обхода: `parent`→out, `related`→in, всё прочее unknown→both."""
    value = str(direction or "").strip().lower()
    if value in {"out", "outgoing", "parent", "up"}:
        return "out"
    if value in {"in", "incoming", "related", "down"}:
        return "in"
    return "both"


def _expand_kinds(kinds: Sequence[str] | None) -> frozenset[str] | None:
    """Нормализует сужение по видам рёбер; None/`any`/`[]` — без сужения."""
    if not kinds:
        return None
    values = {str(item).strip().upper() for item in kinds if str(item).strip()}
    if not values or values == {"ANY"}:
        return None
    return frozenset(values)


# Предел глубины обхода — страховка от неограниченного разворачивания графа. Профиль
# может запросить больше; значение нормализуется одинаково всеми адаптерами, а факт
# урезания сообщается в трассировке конвейера, а не применяется молча.
MAX_EXPANSION_DEPTH = 3


def _expand_depth(max_depth: int) -> int:
    return min(max(int(max_depth), 1), MAX_EXPANSION_DEPTH)

# A-2 (ADR-024): честный контракт атомарности COMMIT. Ровно два значения, без алгебры типов.
Consistency = Literal["atomic", "best_effort"]
VECTOR_METADATA_BACKFILL_KEYS = frozenset(
    {
        "context_ids",
        "tag_ids",
        "source_ids",
        "chunk_ids",
        "aliases",
        "enrichment_origin",
        "projection_revision",
        "projection_retry",
        "custom",
    }
)


class AtomicBatch(Protocol):
    """Обе оси пары в одной транзакции движка (A-2, атомарная пара).

    Поставляется движком-координатором (см. `GraphStoreProvider.atomic_batch`):
    методы графовой и векторной осей выполняются в одном session/begin_transaction.
    """

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None: ...
    def upsert_edges(self, edges: list[dict[str, Any]]) -> None: ...
    def delete_node(self, node_id: str) -> bool: ...
    def upsert_vectors(self, items: list[dict[str, Any]]) -> None: ...
    def delete_vectors(self, chunk_ids: list[str]) -> None: ...
    def remove_source_from_entities(
        self,
        domain: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None: ...


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

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Legacy read path; core retrieval uses expand."""
        raise NotImplementedError("graph adapter does not provide query")

    def expand(
        self,
        context_ids: list[str],
        *,
        direction: str = "both",
        kinds: Sequence[str] | None = None,
        max_depth: int = 2,
        max_fanout: int = 8,
        max_nodes: int = 32,
    ) -> list[dict[str, Any]]:
        """Bounded context expansion; returns empty when unsupported.

        `direction` is `both` (default), `out` or `in`; the legacy values `parent`
        and `related` map to `out` and `in`. Traversal is not restricted by edge kind
        — the written kinds come from extraction and are reported back per row.
        `kinds` optionally narrows traversal to an explicit set.
        """
        return []

    @abstractmethod
    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        """Массовая вставка/обновление узлов (MERGE по node_id)."""

    @abstractmethod
    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        """Массовая вставка/обновление рёбер."""

    @abstractmethod
    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Получение узла по node_id."""

    def verify_edge(self, from_id: str, to_id: str, edge_type: str) -> bool:
        """Проверить наличие ребра после projection upsert."""
        del from_id, to_id, edge_type
        return True

    @abstractmethod
    def delete_node(self, node_id: str) -> bool:
        """Удаление узла по node_id (вместе с инцидентными рёбрами)."""

    def remove_source_from_entities(
        self,
        domain: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None:
        """Убрать soft-deleted source/chunk provenance из доменных узлов."""
        return

    @abstractmethod
    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        """Чанки источника по связи CONTAINS (soft-delete, L2-05)."""

    def transaction(self) -> GraphTx:
        """Атомарная запись пачки изменений (M2, L2-04). По умолчанию — no-op."""
        return nullcontext(self)

    def consistency_capability(self) -> Consistency:
        """Возможность атомарной записи пары (A-2, ADR-024).

        `"atomic"` — ось может участвовать в атомарной записи пары при общем с партнёром
        `engine_key()` (единый движок/координатор). `"best_effort"` — дефолт: атомарность
        пары не обещается. Ядро доверяет декларации, вендора не проверяет."""
        return "best_effort"

    def engine_key(self) -> str | None:
        """Ключ движка/инстанса БД (A-2). Ядро сравнивает ключи пары, формат не разбирает.
        None — уникальный/неизвестный движок: пара никогда не является атомарной."""
        return None

    def atomic_batch(self) -> AbstractContextManager[AtomicBatch] | None:
        """Единая транзакция движка для ОБЕИХ осей (A-2, атомарная пара).

        Контекст, принимающий операции графовой и векторной осей и коммитящий их вместе
        (для Neo4j-пары — один `session.begin_transaction()`). None — движок не
        предоставляет объединённой записи; CommitStage обрабатывает пару вложенными
        `transaction()`-контекстами (единый процесс/журнал)."""
        return None

    def ensure_schema(self, node_types: list[dict[str, Any]]) -> None:
        """Legacy no-op retained for operator migrations; ingest never calls it."""
        return

    def transient_aware(self) -> bool:
        """True — хранилище может бросать transient-ошибки (сеть/deadlock),
        которые имеют смысл ретраить (S1/S2, ADR-028). InMemory — False,
        Neo4j (и сетевые) — True."""
        return False

    def is_transient(self, exc: BaseException) -> bool:
        """Классификация ошибки как повторимой (ADR-028). Вызывается только если
        `transient_aware()`. Ядро не импортирует вендорские пакеты (L1-02) —
        ответственность на провайдере. Реализации могут разворачивать цепочку
        `__cause__` (UC12-02): обёртки адаптеров/ядра не должны ломать
        классификацию исходной вендор-специфичной ошибки."""
        return False


class VectorStoreProvider(ABC):
    """Векторная ось (ADR-013): косинусный поиск по единицам чанков."""

    @abstractmethod
    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        """Косинусный поиск топ-K ближайших чанков."""

    @abstractmethod
    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        """Запись/обновление эмбеддингов (chunk_id -> vector + metadata)."""

    @abstractmethod
    def delete_vectors(self, chunk_ids: list[str]) -> None:
        """Снятие чанков с поиска (soft-delete, L2-05)."""

    def update_vector_metadata(self, updates: list[dict[str, Any]]) -> int:
        """Обновить только metadata существующих vector records.

        В update можно передать ``replace=True``: списки разрешённых
        enrichment-полей заменяются, а не объединяются со старыми значениями.
        """
        del updates
        return 0

    def get_vector_metadata(self, chunk_id: str) -> dict[str, Any] | None:
        """Прочитать metadata vector record для verification backfill."""
        del chunk_id
        return None

    def verify_projection(self, domain: str, projection_revision: str) -> bool:
        """Проверить, что domain vectors принадлежат projection revision."""
        del domain, projection_revision
        return True

    def list_chunk_ids_of_source(
        self,
        source_url: str,
        domain: str | None = None,
    ) -> list[str]:
        """Chunk IDs одного источника для безопасной vector-only re-index."""
        del source_url, domain
        return []

    def transaction(self) -> VectorTx:
        """Атомарная запись пачки эмбеддингов (M2, L2-04). По умолчанию — no-op."""
        return nullcontext(self)

    def consistency_capability(self) -> Consistency:
        """Возможность атомарной записи пары (A-2, ADR-024). См. GraphStoreProvider."""
        return "best_effort"

    def engine_key(self) -> str | None:
        """Ключ движка/инстанса БД (A-2). См. GraphStoreProvider."""
        return None

    def transient_aware(self) -> bool:
        """True — хранилище может бросать transient-ошибки (ADR-028). InMemory — False."""
        return False

    def is_transient(self, exc: BaseException) -> bool:
        """Классификация ошибки как повторимой (ADR-028). См. GraphStoreProvider."""
        return False