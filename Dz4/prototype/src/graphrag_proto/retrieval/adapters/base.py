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

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
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


def _node_properties(value: object) -> dict[str, Any]:
    """Свойства узла приводятся к отображению МОЛЧА, любым способом.

    На живом графе `properties` приходит строкой (`"{}"`), потому что ingest пишет его
    JSON-текстом, а `dict("{}")` падает с ValueError. Это нашлось E2E-прогоном глубины на
    стенде, а не тестом: юнит-тесты строили узлы руками и клали туда отображение, а
    `expand()` на живых данных до этого никто не вызывал - предыдущие E2E-сценарии ходили
    в Neo4j напрямую. Ровно тот класс, что и с `chunk_ids`: механизм написан, на реальных
    данных не исполнялся.

    Отсюда правило: значение, которое не удалось прочитать как отображение, даёт пустое
    отображение, а не исключение. Обход не должен падать из-за чужого свойства.

    Формы, которые действительно приходят от Neo4j, - отображение и JSON-текст (его пишет
    ingest). Ветки для «списка пар» здесь нет намеренно: она была написана на умозаключении
    и оказалась неверной - цели распаковки в компренхшене связываются ДО проверки условия,
    а `Sequence` включает `str` и `bytes`, так что она падала и на словаре, и на байтах.
    """
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _expand_kinds(kinds: Sequence[str] | None) -> frozenset[str] | None:
    """Нормализует сужение по видам рёбер; None/`any`/`[]` — без сужения."""
    if not kinds:
        return None
    values = {str(item).strip().upper() for item in kinds if str(item).strip()}
    if not values or values == {"ANY"}:
        return None
    return frozenset(values)


# Предел глубины обхода — страховка от неограниченного разворачивания графа. Профиль
# и запрос могут просить больше; значение нормализуется одинаково всеми адаптерами, а факт
# урезания сообщается в трассировке конвейера, а не применяется молча.
#
# Шесть, а не три (ADR-036): документированная партономия — цепочка переменной длины
# 4..6, от дома до страны пять шагов (docs/01 §2). Потолок 3 был НИЖЕ минимальной длины
# этой цепочки, то есть он делал недостижимым вопрос «в чём содержится этот дом» и
# усекал DAG с несколькими административными родителями. Стоимость при этом ограничена
# не глубиной, а `max_graph_nodes`; глубина задаёт форму ответа, а не его цену.
MAX_EXPANSION_DEPTH = 6


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
    def delete_orphans(self, domain: str, *, dry_run: bool) -> int: ...


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

    def delete_orphans(self, domain: str, *, dry_run: bool) -> int:
        """Удалить осиротевшие доменные связи и узлы домена (ADR-014, `docs/02` §4.5).

        Осиротевшим считается то, у чего **нет ни одного поддерживающего `chunk_id`**.
        Канонический признак — `chunk_ids`, а не `source_ids`: чанк физически удаляется,
        а источник может остаться жив при отсутствии чанков.

        Три правила, зафиксированные до кода:

        - **Дискриминатор `chunk_ids IS NOT NULL` обязателен.** У структурных рёбер
          (`CONTAINS`, `MENTIONS`) этого свойства нет вовсе, поэтому условие «список пуст»
          для них истинно, и без дискриминатора уборка снесёт их. Ошибка проявится не как
          «не туда удалил», а как «сломался поиск».
        - **Порядок: связи, затем узлы.** Обратный порядок ломает счёт: удалённый узел
          уносит связь, которую мы посчитали удалённой, не удаляя её.
        - **`dry_run=True` ничего не удаляет и возвращает, что было бы удалено.** Подсчёт
          до удаления обязателен: цена ошибки предиката — молчаливая потеря данных в графе,
          которую не откатит ни одна транзакция.

        Возвращает число удалённых связей плюс узлов.
        """
        raise NotImplementedError

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