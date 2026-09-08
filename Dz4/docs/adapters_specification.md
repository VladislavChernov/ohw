# Документация: Слой адаптеров (Adapter Layer) — Подробная спецификация

> **Версия:** v5.1  
> **Последнее обновление:** 2026-09-05  
> **См. также:** [ADR-012](../05_adr_log.md) — Архитектурное решение о введении слоя адаптеров; [ADR-013](../05_adr_log.md) — Разделение интерфейсов графового и векторного хранилищ

---

## 1. Обзор слоя адаптеров

Слой адаптеров — это уровень абстракции, который изолирует ядро системы от конкретных инфраструктурных компонентов. Взаимодействие с хранилищем, LLM, эмбеддером и реранкером происходит исключительно через программные интерфейсы.

### 1.1. Цели

- **Гибкость:** Замена любого компонента — правка одного конфига, без переписывания ядра.
- **Расширяемость:** Сторонние разработчики могут создавать свои адаптеры через entry_points.
- **Снижение vendor lock-in:** Нет жёсткой привязки к конкретному поставщику.
- **Выбор стека:** Подбор оптимальной конфигурации под задачу (лёгкая инсталляция, облачный LLM, замена хранилища).
- **Разделение осей поиска (ISP):** Граф и вектор — независимые ABC-контракты, соединяющиеся только на этапе Context Assembly (см. ADR-013).

### 1.2. Архитектурная роль

```
+----------------------------------------------------------+
|                    ЯДРО СИСТЕМЫ (fixed)                   |
|                                                           |
|  Ingestion Pipeline                                       |
|  Retriever                                                |
|  Services                                                 |
|                                                           |
|  Вызывает методы интерфейсов:                              |
|    graph_store.query()                                    |
|    vector_store.vector_search()                           |
|    llm.generate()                                         |
|    embeddings.embed_batch()                               |
|    reranker.rerank()                                      |
+----------------------------------------------------------+
                                  |
                                  | программный интерфейс (ABC)
                                  v
+----------------------------------------------------------+
|               СЛОЙ АДАПТЕРОВ (runtime config)             |
|                                                           |
|  GraphStoreProvider (ABC)        VectorStoreProvider (ABC)|
|  LLMInference (ABC)                                      |
|  Embedder (ABC)                                           |
|  Reranker (ABC)                                           |
|                                                           |
+----------------------------------------------------------+
                                  |
                                  | конкретная реализация
                                  v
+----------------------------------------------------------+
|             КОНКРЕТНЫЕ РЕАЛИЗАЦИИ (adapters.yaml)        |
|                                                           |
|  Neo4jGraphStore / MemgraphGraphStore                     |
|  Neo4jVectorStore / QdrantVectorStore                     |
|  OllamaAdapter / OpenAICompatibleAdapter / VllmAdapter    |
|  BgeM3ServiceAdapter / LocalSentenceTransformerAdapter    |
|  BgeRerankerAdapter / NoOpRerankerAdapter                 |
+----------------------------------------------------------+
```

---

## 2. Интерфейсы хранилища: GraphStoreProvider и VectorStoreProvider

### 2.1. Описание

Монолитный интерфейс `GraphStorage` **аннулирован** (ADR-013). В гибридной архитектуре граф знаний и векторные эмбеддинги — две принципиально разные операции (логический обход связей vs косинусный поиск), которые должны масштабироваться независимо. Поэтому хранилище разделено на две изолированные оси:

- **`GraphStoreProvider`** — логические связи, обход графа, Cypher-запросы, накат constraint, операции над узлами/рёбрами.
- **`VectorStoreProvider`** — исключительно семантический поиск чанков по сходству векторов и запись эмбеддингов.

В прототипе обе реализации смотрят на Neo4j (native graph engine + native vector index). При росте системы `VectorStoreProvider` бесшовно заменяется на `QdrantVectorStore` без изменений графовой логики ядра.

### 2.2. Контракт GraphStoreProvider (Abstract Base Class)

```python
from abc import ABC, abstractmethod
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

class GraphStoreProvider(ABC):
    @abstractmethod
    def query(self, cypher: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """Выполнение графового Cypher-запроса (обход связей, расширение SIMILAR_TO)."""
        ...

    @abstractmethod
    def upsert_nodes(self, nodes: List[Dict[str, Any]]) -> None:
        """Массовая вставка/обновление узлов."""
        ...

    @abstractmethod
    def upsert_edges(self, edges: List[Dict[str, Any]]) -> None:
        """Массовая вставка/обновление рёбер."""
        ...

    @abstractmethod
    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Получение узла по ID."""
        ...

    @abstractmethod
    def delete_node(self, node_id: str) -> bool:
        """Удаление узла по ID (вместе с инцидентными рёбрами)."""
        ...

    @abstractmethod
    def list_chunk_ids_of_source(self, source_id: str) -> List[str]:
        """(M2) Чанки источника по связи CONTAINS — для soft-delete (L2-05)."""
        ...

    def transaction(self) -> "GraphTx":
        """(M2) Атомарная запись пачки изменений (L2-04). По умолчанию — no-op (nullcontext)."""
        return nullcontext(self)
```

### 2.3. Контракт VectorStoreProvider (Abstract Base Class)

```python
from abc import ABC, abstractmethod
from contextlib import nullcontext
from typing import Any, Dict, List

class VectorStoreProvider(ABC):
    @abstractmethod
    def vector_search(self, embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Косинусный поиск топ-K ближайших текстовых чанков."""
        ...

    @abstractmethod
    def upsert_vectors(self, items: List[Dict[str, Any]]) -> None:
        """Запись/обновление эмбеддингов (chunk_id -> vector + metadata)."""
        ...

    @abstractmethod
    def delete_vectors(self, chunk_ids: List[str]) -> None:
        """(M2) Снятие чанков с поиска — soft-delete (L2-05)."""
        ...

    def transaction(self) -> "VectorTx":
        """(M2) Атомарная запись пачки эмбеддингов (L2-04). По умолчанию — no-op (nullcontext)."""
        return nullcontext(self)
```

### 2.4. Реализации

#### 2.4.1. Neo4jGraphStore (базовая, графовая ось)

- **Драйвер:** Neo4j Bolt-драйвер (официальный).
- **Язык запросов:** Cypher.
- **Роль:** Обход связей, dotted traversal, накат unique constraints, операции узлов/рёбер.
- **Конфигурация:**
  ```yaml
  adapters:
    graph_store: "neo4j_graph"
  storage:
    neo4j_uri: "bolt://neo4j:7687"
  ```

#### 2.4.2. Neo4jVectorStore (базовая, векторная ось)

- **Движок:** Нативный векторный индекс Neo4j.
- **Роль:** Косинусный поиск чанков и запись эмбеддингов.
- **Конфигурация:**
  ```yaml
  adapters:
    vector_store: "neo4j_vector"
  storage:
    neo4j_uri: "bolt://neo4j:7687"
    vector_index: "chunk_embeddings"
  ```

#### 2.4.3. MemgraphGraphStore

- **Драйвер:** Bolt-совместимый драйвер Memgraph (Apache 2.0).
- **Язык запросов:** Cypher.
- **Причина выбора:** Лицензия Apache 2.0 (без GPLv3), ниже потребление ОЗУ.
- **Конфигурация:**
  ```yaml
  adapters:
    graph_store: "memgraph_graph"
  ```

#### 2.4.4. QdrantVectorStore

- **Тип:** Только векторы + метаданные (без графа).
- **Причина выбора:** Горизонтальное масштабирование векторного поиска на миллиардах векторов.
- **Роль:** Полностью заменяет `Neo4jVectorStore` на фазе роста (ADR-001), **не затрагивая** `GraphStoreProvider`.
- **Ограничение:** Графовые запросы не поддерживаются — при использовании данного адаптера графовая ось продолжает работать через `Neo4jGraphStore`/`MemgraphGraphStore`.
- **Конфигурация:**
  ```yaml
  adapters:
    vector_store: "qdrant"
  ```

#### 2.4.5. Neo4jGrpcGraphStore (production-кластеры)

- **Драйвер:** gRPC-драйвер для production-кластеров Neo4j.
- **Преимущество:** Выше пропускная способность, кластерная маршрутизация.
- **Конфигурация:**
  ```yaml
  adapters:
    graph_store: "neo4j_grpc_graph"
  ```

### 2.5. Ограничения и правила

- В гибриде граф и вектор работают всегда в паре: `graph_store` + `vector_store` выбираются независимо в `namespace: adapters`.
- Смена хранилища достигается подключением адаптера своей оси (граф/вектор раздельно, ADR-013); перенос данных оси выполняет оператор инсталляции — вне ядра.
- Векторная ось не отключает графовую: Qdrant замещает только `vector_search`/`upsert_vectors`, обход графа остаётся на `GraphStoreProvider`.
- Контрактные тесты обязательны для каждой реализации обоих интерфейсов (ADR-012).

### 2.6. Расширение M2: атомарная запись и soft-delete

Вводится в M2 (L2-04/L2-05, ADR-023) вместе с Ingestion COMMIT в реальные хранилища.

#### 2.6.1. Атомарная запись (`transaction()`)

`transaction()` возвращает context manager с самим хранилищем; изменение применяется **на успешном выходе** из блока, при исключении — откатывается целиком (не создаёт частичное состояние). Вложенные `with graph.transaction(), vector.transaction()` в Ingestion/COMMIT дают атомарность граф+вектор в пределах прототипа.

```python
with graph_store.transaction() as gtx, vector_store.transaction() as vtx:
    gtx.upsert_nodes(nodes)
    gtx.upsert_edges(edges)
    vtx.upsert_vectors(vectors)   # исключение -> откат обеих осей
```

#### 2.6.2. Схемы payload'ов COMMIT

| Метод | Элемент | Поля |
|---|---|---|
| `upsert_nodes` | узел | `node_id` (str), `labels` (list[str]), `properties` (dict) |
| `upsert_edges` | ребро | `from_id` (str), `to_id` (str), `type` (str), `properties` (dict) |
| `upsert_vectors` | вектор | `chunk_id` (str), `embedding` (list[float]), `metadata` (dict) |
| `vector_search` | hit | `chunk_id`, `score`, `embedding`, `metadata` |

MERGE-семантика по `node_id`/`chunk_id` (идемпотентность). Node ID для ядра системы (M2): `src:{domain}:{source_url}` (Source), `ent:{domain}:{canonical_name}` (Entity), `chk:{digest12(source_url:index)}` (Chunk). Графовая и векторная оси связаны по `chunk_id` (общему для узла Chunk и записи вектора).

#### 2.6.3. Soft-delete (L2-05)

`GraphStoreProvider.list_chunk_ids_of_source(source_id)` — чанки по ребру CONTAINS; `delete_node(chunk_id)` удаляет узел и инцидентные рёбра (в том числе CONTAINS); `VectorStoreProvider.delete_vectors(chunk_ids)` снимает те же чанки с поиска. Узлы Entity и Source при soft-delete **сохраняются** (историчность, ADR-014); эмиссия `SIMILAR_TO` при этом не затрагивается.