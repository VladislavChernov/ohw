# Документация: Подключение внешних систем через слой адаптеров (Adapter Guide)

> **Версия:** v5.1  
> **Последнее обновление:** 2026-09-05  
> **См. также:** [CONCEPT.md §2](../CONCEPT.md) — слой адаптеров; [ADR-012](./05_adr_log.md) — введение слоя; [ADR-013](./05_adr_log.md) — разделение Graph/Vector осей; [adapters_specification.md](./adapters_specification.md) — контракты интерфейсов.

---

## 1. Зачем нужен этот гайд

Слой адаптеров делает ядро системы **независимым от конкретных технологий** (ADR-012). Владелец инсталляции сам решает, какие внешние системы использовать: хранилища, LLM, эмбеддинги, реранкеры. Закреплённые в ядре программные интерфейсы (`GraphStoreProvider`, `VectorStoreProvider`, `LLMInference`, `Embedder`, `Reranker`) — это «разъёмы», к которым подключаются реализации без переписывания ядра.

Данный гайд описывает путь подключения **внешней системы** как собственного адаптера: от выбора интерфейса до runtime-переключения и отката.

**Ключевое правило (ADR-013):** хранилище — это **две независимые оси** (`graph_store` + `vector_store`). Они переключаются раздельно и соединяются только на этапе Context Assembly.

---

## 2. Пять точек расширения

| Интерфейс (ABC) | Ось / роль | Базовые реализации | Типичная внешняя система |
|-----------------|------------|--------------------|---------------------------|
| `GraphStoreProvider` | Графовая ось: Cypher, связи, обход | `Neo4jGraphStore`, `MemgraphGraphStore`, `Neo4jGrpcGraphStore` | Memgraph, ArangoDB |
| `VectorStoreProvider` | Векторная ось: `vector_search`, `upsert_vectors` | `Neo4jVectorStore`, `QdrantVectorStore` | Qdrant, Milvus, Weaviate, PGVector |
| `LLMInference` | Генерация + extraction | `OllamaAdapter`, `OpenAICompatibleAdapter`, `VllmAdapter` | vLLM, LM Studio, OpenAI-совместимые серверы |
| `Embedder` | Расчёт эмбеддингов | `BgeM3ServiceAdapter`, `LocalSentenceTransformerAdapter`, `OpenAIEmbeddingsAdapter` | sentence-transformers, OpenAI Embeddings API |
| `Reranker` | Переранжирование результатов | `BgeRerankerAdapter`, `NoOpRerankerAdapter`, `CohereRerankAdapter` | Cohere Rerank |

---

## 3. Пять шагов подключения внешней системы

### Шаг 1. Определить контракт (ось) системы

Внешняя система подключается к одному из пяти ABC-интерфейсов. Выбор определяется тем, **что умеет система**:

- умеет Cypher и логику связей → `GraphStoreProvider`;
- умеет только косинусный поиск векторов → `VectorStoreProvider`;
- генерирует текст → `LLMInference`; считает векторы → `Embedder`; переранжирует → `Reranker`.

> Не пытайтесь «уместить» систему не в её ось: векторная БД без графа **не может** реализовать `GraphStoreProvider` (ADR-013), а графовая без векторного индекса — `VectorStoreProvider`.

### Шаг 2. Реализовать ABC-интерфейс

Создайте класс, реализующий абстрактные методы контракта. Параметры подключения (URL, коллекция/индекс, credentials) читаются из рантайм-конфига — секции `storage` (аналогично `neo4j_uri` у Neo4j-адаптеров). Пример — в разделе 4.

### Шаг 3. Написать контрактные тесты (обязательно)

ADR-012 требует **контрактные тесты для каждой реализации**. Тесты гоняются против реальной внешней системы (staging / testcontainers) и проверяют инварианты контракта, а не внутренности реализации. Без прохождения контрактных тестов адаптер не принимается.

Пример набора для `VectorStoreProvider`:
- `upsert_vectors` → `vector_search` находит записанный чанк;
- `vector_search` возвращает не более `top_k` результатов;
- результаты отсортированы по убыванию близости к запросу;
- метаданные чанка сохраняются и возвращаются в результатах.

### Шаг 4. Зарегистрировать адаптер через `entry_points`

Регистрация — через Python setuptools, без изменения кода ядра (CONCEPT.md §2.6). Имя, указанное в `entry_points`, — это значение ключа в конфиге:

```toml
# pyproject.toml стороннего пакета
[project.entry-points."graphrag.adapters.storage"]
my_vector_db = "my_package.adapters.myvectordb:MyVectorDBStore"
```

Ядро автоматически загружает адаптер по имени из `entry_points` при старте.

### Шаг 5. Включить адаптер в рантайм-конфиг

Имя адаптера указывается в `namespace: adapters` **под ключом своей оси**. Доступные ключи (только они): `graph_store`, `vector_store`, `llm`, `embeddings`, `reranker`.

```yaml
# prototype/infra/config/adapters.yaml
adapters:
  vector_store: "my_vector_db"   # имя из entry_points
storage:
  myvectordb_url: "http://myvectordb:6333"
  myvectordb_collection: "chunks"
```

Переключение на лету, без перезапуска контейнеров:

```bash
curl -X PUT http://localhost:8001/api/v1/config/adapters \
  -d '{"vector_store": "my_vector_db"}'
```

Проверка текущего состояния и доступных адаптеров (включая плагины):

```bash
curl http://localhost:8001/api/v1/config/adapters
curl http://localhost:8001/api/v1/config/adapters/available
```

> **Совет.** Сменить решение — просто подключить адаптер своей оси и переключиться (`PUT /config/adapters`); граф и вектор меняются раздельно. Если хранилище меняете, перенос данных соответствующей оси выполняет оператор инсталляции — в ядро эта миграция **не входит**.

---

## 4. Полный пример: подключение внешней векторной БД

Допустим, есть внешняя векторная БД с HTTP API (`search`/`upsert`). Подключаем её как `VectorStoreProvider`.

### 4.1. Реализация контракта

```python
# my_package/adapters/myvectordb.py
from typing import Any, Dict, List

from graphrag.adapters import VectorStoreProvider  # фактический путь контракта
from my_package.clients import MyVectorDBClient


class MyVectorDBStore(VectorStoreProvider):
    """Адаптер внешней векторной БД: только векторная ось."""

    def __init__(self, base_url: str, collection: str = "chunks") -> None:
        self._client = MyVectorDBClient(base_url, collection)

    def vector_search(self, embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        hits = self._client.search(embedding, limit=top_k)
        return [
            {"chunk_id": h.id, "score": h.score, "metadata": h.metadata}
            for h in hits
        ]

    def upsert_vectors(self, items: List[Dict[str, Any]]) -> None:
        payload = [(i["chunk_id"], i["vector"], i.get("metadata", {})) for i in items]
        self._client.upsert(payload)
```

### 4.2. Регистрация

```toml
# my_package/pyproject.toml
[project.entry-points."graphrag.adapters.storage"]
my_vector_db = "my_package.adapters.myvectordb:MyVectorDBStore"
```

### 4.3. Конфигурация и переключение

```yaml
# prototype/infra/config/adapters.yaml
adapters:
  vector_store: "my_vector_db"   # было "neo4j_vector"
storage:
  myvectordb_url: "http://myvectordb:6333"
  myvectordb_collection: "chunks"
```

```bash
# запуск ядра подхватит адаптер и прочитает параметры из storage
curl -X PUT http://localhost:8001/api/v1/config/adapters \
  -d '{"vector_store": "my_vector_db"}'

# убедиться, что адаптер доступен и выбран
curl http://localhost:8001/api/v1/config/adapters/available
curl http://localhost:8001/api/v1/config/adapters
```

### 4.4. Откат

Переключение обратно на базовую реализацию той же оси:

```bash
curl -X PUT http://localhost:8001/api/v1/config/adapters \
  -d '{"vector_store": "neo4j_vector"}'
```

---

## 5. Типичные ошибки

| Ошибка | Почему неправильно |
|--------|--------------------|
| Использовать ключ `"storage"` в payload | В `namespace: adapters` такого ключа нет. Хранилище разделено на оси — только `graph_store` и `vector_store`. |
| Переключать граф на векторный адаптер (и наоборот) | Qdrant не поддерживает Cypher, а векторный индекс Neo4j не «притворяется» графом (ADR-013). |
| Пропустить контрактные тесты | ADR-012 требует их для каждой реализации. |
| Имя в `adapters.yaml` отличается от имени в `entry_points` | Ядро не найдёт класс по имени — адаптер не загрузится. |
| Параметры подключения писать в `adapters.*` вместо `storage` | Смешение селектора адаптера и параметров подключения ломает `GET /api/v1/config/adapters/available`. |

**Золотое правило:** ключ в конфиге — это **что заменить** (ось), имя в `entry_points` — **чем заменить** (реализация), а параметры соединения живут в `storage` рядом с `adapters` в `prototype/infra/config/adapters.yaml`.