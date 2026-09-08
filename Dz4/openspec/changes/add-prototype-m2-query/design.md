# Design: add-prototype-m2-query

## Решения

### D1. Task Queue — интерфейс и две реализации (Valkey/Redis Streams + InMemory)

- ABC `TaskQueue`:
  - `submit(domain, query, metadata) -> task_id` (XADD в stream `query:tasks`);
  - `claim(worker_id) -> Optional[Task]` (XREADGROUP `consumer_group=query-workers`,
    блокирующее чтение);
  - `ack(task_id)` / `fail(task_id, error)` (XACK / запись статуса);
  - `get_status(task_id) -> TaskStatus` (SQLite `query_tasks`);
  - `events(task_id) -> Iterator[Event]` — чтение пер-task stream событий
    `query:events:{task_id}` (XREAD, for SSE-подписчика).
- `RedisStreamTaskQueue` — рабочая (Valkey, `redis>=5`). `InMemoryTaskQueue` — тот же
  контракт, блокирующая очередь на `queue.Queue` + `collections.deque` событий.
  Выбор через env `QUERY_QUEUE=redis|inmemory` (compose `llm`-профиль → `redis`;
  тесты → `inmemory`).
- **Почему Streams, а не pub/sub:** нужна per-task история событий для подписчика SSE,
  пришедшего позже начала обработки, и consumer-group для пула воркеров.
- Task id: `q_<hex8>` (как в `api_reference.md §3.1`).

### D2. Query API — эндпоинты и контракт

- `src/graphrag_proto/query_service/app.py` (:8000), entry `graphrag-query`:
  - `POST /query` (X-API-Key) → `202 {task_id, status: "queued", accepted_at}`; валидация
    тела (`query` — непустая строка, `metadata.domain` — опция; 422 при нарушении).
  - `GET /query/tasks/{task_id}` → `{task_id, status, stage}` (lifecycle
    `queued → running → succeeded | failed | cancelled`).
  - `GET /query/tasks/{task_id}/stream` → **SSE**: события `EventEnvelope` по ADR-016
    (`status`/`token`/`done`/`error`), однострочный JSON в `data:`.
  - `GET /health` — healthcheck.
- X-API-Key-зависимость — из `namespace: auth.api_key` → Config Service (fallback
  `changeme`), как в остальных сервисах (L5-01). Ролевая матрица — отдельная стадия.
- Web-страница SSE закрытия соединения → задача продолжает выполняться в очереди
  (worker не зависит от HTTP-клиента); статус можно допросить `GET /tasks/{id}`.

### D3. Query Worker и ретривер-пайплайн

- `src/graphrag_proto/retrieval/pipeline.py` — `QueryPipeline.run(task)`, шаги
  `docs/03_retriever.md`:
  1. `status: embedding` — `Embedder.embed(query)` (M2: `DeterministicEmbedder`,
     стабильный хэш-вектор; без GPU);
  2. `status: graph` ∥ `status: vector` — параллельно через пул потоков
     (`GraphRetriever` по параметризованному Cypher-шаблону Domain Profile и
     `VectorRetriever.vector_search(embedding, top_k=5)`); независимы (L1-04);
  3. `status: rerank` — `Reranker.rerank` (M2: `NoOpRerankerAdapter`);
  4. **ContextAssembly** — `context.py`: скелет (graph)-результаты первыми (L3-03),
     затем vector-чанки (отсортированные reranker-score); жёсткий лимит 4096 токенов
     (L3-04); при переполнении — детерминированное вытеснение векторных чанков по
     убыванию score, скелет не трогается;
  5. `status: llm` — `LLMInference.generate(prompt)`; стриминг: каждый фрагмент →
     `token`-событие. M2: `OpenAICompatibleAdapter` (`/v1/chat/completions` llama.cpp,
     `LLM_BASE_URL=http://llm:8080`), в тестах — `FakeLLM`;
  6. `done` — `{text, sources[{source_url, relevance}], generation_time_s}`.
- Worker (`graphrag-query-worker`): цикл `claim → run → publish` с ack/fail и обработкой
  отмены (статус `cancelled`): отменённые до старта задачи пропускаются при claim
  (entry ack'ается); уже запущенную задачу воркер доводит до конца и фиксирует
  флаг отмены после (строгая остановка середины пайплайна — вне M2).
- Тайминги шагов — в `done` (поле `generation_time_s`), детерминизм генерации текста
  вне скоупа (LLM — не для детерминизма, L1-05 исключает LLM из критичных шагов).

### D4. Интерфейсы адаптеров (M2-минимум) и реализации

Адаптерные ABC по `docs/adapters_specification.md §2` + `L1-02`:

```python
class Embedder(ABC):        def embed(self, text: str, domain: str) -> list[float]: ...
class Reranker(ABC):        def rerank(self, query, chunks) -> list[float]: ...
class LLMInference(ABC):    def generate(self, prompt, system="", stream=False): ...
class GraphStoreProvider(ABC):
    def query(self, cypher, params) -> list[dict]: ...
    def upsert_nodes(self, nodes) -> None: ...
    def upsert_edges(self, edges) -> None: ...
class VectorStoreProvider(ABC):
    def vector_search(self, embedding, top_k=5) -> list[dict]: ...
    def upsert_vectors(self, items) -> None: ...
```

Реализации M2 (в `retrieval/adapters/`):
- `DeterministicEmbedder` — стабильный хэш-вектор (без GPU, L4-01).
- `NoOpRerankerAdapter` — возвращает вход как есть.
- `OpenAICompatibleAdapter` — llama.cpp `/v1` (порт 8080, API-совместимый).
- `InMemoryGraphStore`/`InMemoryVectorStore` — контрактные тесты без внешних систем.
- `Neo4jGraphStore`/`Neo4jVectorStore` — `bolt://neo4j:7687` (драйвер `neo4j`);
  VectorStore в M2 — косинусная близость по проперти `embedding` в Cypher
  (native vector index — M3).

### D5. Реальный COMMIT (M1-заглушка → провайдеры)

- `CommitStage` вместо M1-stub: вызов `GraphStoreProvider.upsert_nodes/upsert_edges` +
  `VectorStoreProvider.upsert_vectors` атомарно (одна транзакция, L2-04):
  - узлы-сущности: MERGE по `canonical_name`, пропсы `source_ids`, `extractor_version`
    (L2-01);
  - рёбра `CONTAINS` (чанк→источник, L2-03), `SIMILAR_TO` (merge-пары из DEDUP);
  - чанк-узлы + эмбеддинги (вектор из детерминированного фейка M1-EMBED);
  - soft-delete: снятие `CONTAINS`/векторов чанков (L2-05) — в этой же вехе.
- Интеграция с пайплайном M1: этап COMMIT получает `GraphStoreProvider`+`VectorStoreProvider`
  из контейнера зависимостей (env `GRAPH_STORE=neo4j|inmemory`, `VECTOR_STORE=neo4j|inmemory`).
  Registry (SQLite) по-прежнему источник версий/идемпотентности (ADR-014).

### D6. Compose

| Сервис | Порт / образ | Профиль | Зависимости |
|---|---|---|---|
| `valkey` | `valkey/valkey:9.1.2` | `llm` | — |
| `query-api` | :8000, `ohw/query-api:prototype` | `llm` | valkey, config, glossary, neo4j (данные), llm (опционально) |
| `query-worker` | `ohw/query-worker:prototype`, команда `graphrag-query-worker` | `llm` | valkey, graph (neo4j), llm (опционально) |

- Healthcheck'и: `query-api` — `GET /health`; `valkey` — `redis-cli ping`.
- `llm`-профиль не конфликтует с GPU: в M2 эмбеддер/реранкер — без GPU (L4-01).
- Все образы сторонних сервисов — с пинованными тегами версий (политика M1).

## Модули

| Модуль | Изменение |
|---|---|
| `docs/05_adr_log.md` | + ADR-023 (Task Queue / Redis Streams / события задач) |
| `docs/api_reference.md` §3 | путь SSE `GET /query/tasks/{task_id}/stream`, task store |
| `docs/web_layer_replacement.md` | сверка путей эндпоинтов (не противоречит) |
| `openspec/changes/add-prototype-m2-query/*` | proposal/design/tasks/spec (этот бандл) |
| `prototype/src/graphrag_proto/query_service/` | app (:8000), task_queue, store |
| `prototype/src/graphrag_proto/retrieval/` | pipeline, retrievers, context, adapters |
| `prototype/src/graphrag_proto/ingestion_service/` | CommitStage → провайдеры; soft-delete чанков |
| `prototype/pyproject.toml` | + `redis`, `neo4j`; scripts `graphrag-query`, `graphrag-query-worker` |
| `prototype/tests/` | test_query_api, test_query_pipeline, test_task_queue, test_retriever, test_commit |
| `prototype/infra/compose.yaml` | valkey (:6379), query-api (:8000), query-worker; пин `valkey/valkey:9.1.2` |

## Open questions

- OQ1. WebSocket-транспорт в M2 не делаем (SSE достаточно для контракта ADR-016);
  WS подключится вместе с MCP-шлюзом (M5). Допустимо? — фиксируем в ADR-023.
- OQ2. Векторный поиск M2 — косинусная близость по проперти в Cypher (без native vector
  index). Для небольшого числа чанков приёмлемо; native index — M3.
- OQ3. `llm`-профиль требует Neo4j с данными (записанными реальным COMMIT). Для
  автономного демо без Neo4j — `GRAPH_STORE=inmemory` + скрипт загрузки фикстур
  (`infra/scripts/seed_demo.py`, опционально).