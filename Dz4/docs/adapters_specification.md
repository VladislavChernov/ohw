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
        """Удаление узла по ID."""
        ...
```

### 2.3. Контракт VectorStoreProvider (Abstract Base Class)

```python
from abc import ABC, abstractmethod
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