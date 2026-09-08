# Proposal: Веха 2 — Query API (асинхронный контур) + Retriever

> Продолжение ДЗ4 после M1 (`add-prototype-m1-ingestion`, `55914ff`).
> Строится на M1: Ingestion Pipeline (9 этапов), канонический документ (ADR-021),
> DocumentRegistry — уже в контуре. Данная веха = раздел «Веха 2 — Query API (асинхронный
> контур)» из `docs/prototype_requirements.md §6` + доведение data plane до реального
> COMMIT в Neo4j (открытый вопрос M1: «реальная Neo4j-атомарность — M2+»).
> Security-бандл — отдельная стадия (по решению пользователя), в M2 только документирование.

## Зачем

M1 сделал загрузку документов (INGEST→…→COMMIT), но **нет поискового контура**: не у чего
спрашивать систему. Чтобы M3 (боевые адаптеры) и M4 (eval-гейт) имели смысл, нужен
сквозной **Query API** по `api_reference.md §3` (ADR-016/020): приём задачи → `202 + task_id`,
обработка пулом Query Workers через Task Queue (Valkey / Redis Streams), доставка результата
потоком `status/token/done/error` через SSE. Это «критерий готовности §8.2»

Дополнительно: M1 зафиксировал COMMIT-заглушку (DocumentRegistry). Ретриверу нечего искать,
пока COMMIT не пишет в граф/вектор реально. Поэтому в этой вехе COMMIT доводится до
настоящей атомарной записи в Neo4j (L2-04, L2-03) через интерфейсы адаптеров
(`GraphStoreProvider`/`VectorStoreProvider`, ADR-012/013).

## BR (бизнес-требования)

- **BR-1. Асинхронный Query API.** `POST /query` → `202 {task_id, status, accepted_at}`;
  `GET /query/tasks/{task_id}` с lifecycle `queued → running → succeeded | failed | cancelled`
  (L3-05, ADR-016, `api_reference.md §3`).
- **BR-2. Task Queue (Valkey / Redis Streams).** Вызовы обрабатывает пул Query Workers через
  очередь (XADD/XREADGROUP/XACK); доставка результата — SSE единым конвертом событий ADR-016.
- **BR-3. Детерминированный Retriever.** Графовый «скелет» всегда вставляется первым (L3-03);
  лимит контекста 4096 токенов (L3-04); при переполнении вытесняются только векторные чанки
  (по убыванию reranker-score). Детерминизм — Python, без LLM (L1-05).
- **BR-4. Граф ∥ вектор (L1-04).** Две независимые оси поиска, соединяются только на
  Context Assembly (ADR-013).
- **BR-5. Реальный COMMIT в Neo4j.** Атомарная запись узлов/рёбер и векторных пропсов чанков
  (L2-04, L2-03, L2-01); soft-delete снимает чанки с поиска (L2-05). Ядро общается с графом
  только через интерфейсы (L1-02).

## Что делаем

- **Контракт:** `docs/05_adr_log.md` ADR-023 «Task Queue прототипа и доставка событий
  (Redis Streams / Valkey)» + уточнение `docs/api_reference.md §3` (путь SSE-стрима,
  task store). ADR-016/020 уже зафиксированы — используем как есть.
- **Task Queue (`query_service/task_queue.py`):** ABC `TaskQueue` + `RedisStreamTaskQueue`
  (Valkey Streams: очередь `query:tasks`, consumer-group=worker, пер-task stream событий
  `query:events:{task_id}`) + `InMemoryTaskQueue` (тот же контракт, для unit-тестов).
  Status-сторы: SQLite (`query_tasks`) в `query_service/store.py`.
- **Query API (:8000, FastAPI, ADR-020):** `POST /query` → 202; `GET /query/tasks/{task_id}`;
  `GET /query/tasks/{task_id}/stream` — SSE (`EventEnvelope{type: status|token|done|error,
  task_id, ts, payload}`); X-API-Key auth (L5-01). Entry `graphrag-query`.
- **Query Worker (`graphrag-query-worker`):** читает задачу из Streams, прогоняет 7 шагов
  `docs/03_retriever.md`, публикует события: `status` (embedding|graph|vector|rerank|llm),
  `token*`, `done` ({text, sources[{source_url, relevance}], generation_time_s}).
- **Ретривер (`retrieval/`):** `pipeline.py` (оркестрация шагов), `retrievers.py`
  (GraphRetriever по Cypher-шаблону DOMAIN-агностично ∥ VectorRetriever top-K),
  `context.py` (ContextAssembly: 4096, скелет-первый, вытеснение по reranker-score),
  `adapters/base.py` (ABC: `Embedder`, `Reranker`, `LLMInference`, `GraphStoreProvider`,
  `VectorStoreProvider`). Реализации M2: `DeterministicEmbedder`, `NoOpRerankerAdapter`,
  `OpenAICompatibleAdapter` (llama.cpp `/v1/chat/completions`, `LLM_*` env из M1),
  `InMemoryGraphStore`/`InMemoryVectorStore` (тесты), `Neo4jGraphStore`/`Neo4jVectorStore` (бой).
  Боевые bge-m3 / bge-reranker / native vector index Neo4j — **M3**.
- **Реальный COMMIT:** `CommitStage` (M1-заглушка) заменяется на запись через
  `GraphStoreProvider` + `VectorStoreProvider`: MERGE узлов по `canonical_name` с
  `source_ids`/`extractor_version` (L2-01), рёбра `CONTAINS`/`SIMILAR_TO` (L2-03),
  эмбеддинги чанков атомарно, в одной транзакции (L2-04). Композ-признак: оба провайдера
  смотрят на Neo4j `bolt://neo4j:7687`.
- **Compose:** `valkey` (профиль `llm`, образ `valkey/valkey:9.1.2` — пин версии),
  `query-api` (:8000, healthcheck `GET /health`), `query-worker` (тот же профиль);
  depends_on valkey/neo4j.
- **Демо-данные:** для сквозной проверки — фикстуры InMemory-провайдеров (тесты);
  на живой инсталляции — данные, записанные реальным COMMIT из M2-pipeline.

## Не делаем в этой вехе

- Боевые EMBED/EXTract (bge-m3, Qwen) и bge-reranker-base — **M3** (в M2 детерминированные
  фейки / NoOp, без GPU — L4-01 остаётся ненарушенным).
- Native vector index Neo4j и Qdrant, runtime-переключение адаптеров
  (`PUT /config/adapters`, Topology Orchestrator :8005) — **M3**.
- WebSockets-транспорт (в M2 доставка через SSE; WS — тем же конвертом ADR-016, опция M5/MCP).
- MCP-шлюз (ADR-017) и rate-limiting (`docs/security.md §4`) — запланировано на M5/отдельную
  стадию. В M2 — X-API-Key-аутентификация как во всех контурах (L5-01), без ролевой матрицы.
- Security-бандл (`docs/security.md §5`: redaction, TLS, RBAC-UI, ротация; allowed_roles/JWT/PII;
  источник ролей в финале — внешний каталог AD/LDAP) — **отдельная стадия после M3, перед M4**
  (решение 2026-09-08). В M2 входит только X-API-Key (L5-01); прототип — один ключ + матрица
  ролей в docs.

## Проверка (критерии приёмки M2)

- Сквозной вызов `POST /query` → `202 {task_id}`; `GET /query/tasks/{task_id}` показывает
  lifecycle; SSE-стрим `GET /query/tasks/{task_id}/stream` отдаёт `status → token* → done`
  с `text`, `sources`, `generation_time_s` по ADR-016; при сбое — `error`.
- Retriever: граф ∥ вектор выполняются как независимые оси; сборка контекста ставит
  «скелет» первым; лимит 4096 не превышается; при переполнении вытесняются векторные чанки
  с наименьшим reranker-score (L3-03/L3-04).
- COMMIT: в провайдере присутствуют узлы-сущности с `canonical_name` и `source_ids`,
  рёбра `CONTAINS`; повторный INGEST — no-op; soft-delete снимает чанки с поиска.
- `uv run pytest -q`, `uv run ruff check`, `uv run mypy` — зелёные (самодостаточные тесты,
  без внешнего Valkey/Neo4j: InMemory-контрактные тесты).
- Документация: ADR-023 записан; `api_reference.md §3` и `web_layer_replacement.md` не
  противоречат (путь SSE, контракт задач).