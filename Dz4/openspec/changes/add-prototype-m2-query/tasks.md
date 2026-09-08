# Задачи: M2 (add-prototype-m2-query)

> Toolchain — dev-контейнер `ohw-python:3.13` (Docker) или ВМ.
> `[x]` — только после зелёной проверки (`uv run pytest/ruff/mypy`).
> Тесты — самодостаточные (InMemory-реализации без внешнего Valkey/Neo4j).

## 1. Контракт (ADR-023 + дока)

- [x] 1.1. `docs/05_adr_log.md`: ADR-023 «Task Queue прототипа (Redis Streams / Valkey)
      и доставка событий задач»: очереди `query:tasks`/`query:events:{task_id}`,
      consumer-group, путь SSE.
- [x] 1.2. `docs/api_reference.md` §3: путь `GET /query/tasks/{task_id}/stream`,
      task store (SQLite `query_tasks`), `GET /health`.
- [x] 1.3. Сверка `docs/web_layer_replacement.md` (пути эндпоинтов не противоречат).
- [x] 1.4. `openspec/.../m2-query`: proposal/design/tasks/spec (этот бандл).

## 2. Task Queue (Valkey / Redis Streams)

- [x] 2.1. ABC `TaskQueue` (submit/claim/ack/fail/publish/events/is_cancelled/cancel)
      — статусы в `TaskStore` (`queued|running|succeeded|failed|cancelled`).
- [x] 2.2. `RedisStreamTaskQueue`: XADD `query:tasks`; XREADGROUP
      (consumer `query-workers`); XADD/XACK событий `query:events:{task_id}`; task_id `q_<hex8>`.
- [x] 2.3. `InMemoryTaskQueue` — тот же контракт (очередь + per-task события);
      выбор через env `QUERY_QUEUE=redis|inmemory`.
- [x] 2.4. Task store: SQLite `query_tasks` (`status`, `stage`, `created_at`, `error`).

## 3. Query API (:8000)

- [x] 3.1. FastAPI `query_service` (:8000), script `graphrag-query-api`,
      `GET /health` → healthcheck.
- [x] 3.2. `POST /query` → `202 {task_id, status: queued, accepted_at}`;
      валидация тела (`query` непустая; 422 при нарушении); X-API-Key (401, L5-01).
- [x] 3.3. `GET /query/tasks/{task_id}` → lifecycle `queued → running → succeeded|failed|cancelled`.
- [x] 3.4. `GET /query/tasks/{task_id}/stream` — SSE, конверт ADR-016
      (`status`/`token`/`done`/`error`); разрыв соединения клиента не останавливает задачу.

## 4. Query Worker и ретривер

- [x] 4.1. Worker CLI `graphrag-query-worker`: `claim → run → publish` потоком.
- [x] 4.2. `retrieval/pipeline.py`: 6 шагов (embedding → graph ∥ vector → rerank →
      context → llm), события `status`, стриминг `token*`.
- [x] 4.3. `retrievers.py`: GraphRetriever (Cypher-шаблон по Domain Profile) ∥
      VectorRetriever (top_k=5).
- [x] 4.4. `context.py`: ContextAssembly — «скелет» первый (L3-03), лимит 4096 (L3-04),
      вытеснение векторных чанков по наименьшему reranker-score.
- [x] 4.5. `done`-событие: `{text, sources[{source_url, relevance}], generation_time_s}`.

## 5. Адаптеры (ABC + реализации M2)

- [x] 5.1. ABC: `Embedder`, `Reranker`, `LLMInference`, `GraphStoreProvider`,
      `VectorStoreProvider` (по `docs/adapters_specification.md §2.2/§2.3`, L1-02;
      M2-расширение `transaction()`/`list_chunk_ids_of_source`/`delete_vectors` — §2.6).
- [x] 5.2. `DeterministicEmbedder` (стабильный хэш-вектор, без GPU), `NoOpRerankerAdapter`.
- [x] 5.3. `InMemoryGraphStore`/`InMemoryVectorStore` — контрактные тесты
      (upsert/query/search/delete/transaction/soft-delete).
- [x] 5.4. `Neo4jGraphStore`/`Neo4jVectorStore` (`bolt://neo4j:7687`, драйвер `neo4j`);
      vector-search M2 — косинус по проперти `embedding` (native index — M3).
- [x] 5.5. `OpenAICompatibleAdapter` (llama.cpp `/v1/chat/completions`, `LLM_*` env),
      `FakeLLM` для тестов (пробелы сохраняются: `"".join(deltas) == text`).

## 6. Реальный COMMIT (M1-заглушка → провайдеры)

- [x] 6.1. `CommitStage`: MERGE узлов-сущностей по `canonical_name`
      (`source_ids`, `extractor_version`), рёбра `CONTAINS`, эмбеддинги чанков —
      атомарно (L2-04, `transaction()` обеих осей; rollback при ошибке).
- [x] 6.2. Выбор провайдеров через env `GRAPH_STORE`/`VECTOR_STORE` (neo4j|inmemory);
      поддержка фикстур M1 (InMemory) без регресса (все M1-тесты зелёные).
- [x] 6.3. Soft-delete: `DELETE /api/v1/ingestion/documents?domain=&source_url=`
      + `soft_delete_source` — снятие чанков удалённого источника с поиска (L2-05).

## 7. Compose и данные

- [x] 7.1. `valkey` сервис (профиль `llm`, образ `valkey/valkey:9.1.2`, healthcheck
      `valkey-cli ping`).
- [x] 7.2. `query-api` (:8000, healthcheck `GET /health`) + `query-worker`
      (команда `graphrag-query-worker`) в профиле `llm`, depends_on valkey/config/neo4j.
- [x] 7.3. env-соединение с Config Service (domain active) и Glossary (resolve) —
      как в M1-сервисах.
- [ ] 7.4. (опц.) скрипт `infra/scripts/seed_demo.py` для автономного демо
      (`GRAPH_STORE=inmemory`) — вне M2 (smoke покрыт тестами 8.2 на InMemory-стеке).

## 8. Верификация

- [x] 8.1. `uv run pytest -q` (100 passed), `uv run ruff check`, `uv run mypy` — зелёные.
- [x] 8.2. Smoke-тесты: сквозной `POST /query → 202 → SSE status/token/done` на
      InMemory-стеке; ошибка → `error`; отмена → `cancelled`.
- [x] 8.3. Контрактные тесты ретривера: «скелет-первый», лимит 4096, вытеснение по score.
- [x] 8.4. Критерии приёмки `proposal.md` (M2) выполнены (проверено тестами:
      SSE lifecycle+done, error, отмена, «скелет-первый»/лимит/вытеснение, COMMIT).
- [ ] 8.5. `/review`, коммит + push `origin master`.

## Открытые вопросы (вне M2)

- OQ1. WebSockets-транспорт — с MCP-шлюзом (M5), тем же конвертом ADR-016.
- OQ2. Боевые bge-m3 / bge-reranker / native vector index / Qdrant + runtime-переключение
      (`PUT /config/adapters`, Topology Orchestrator :8005) — M3.
- OQ3. Security-бандл — отдельная стадия **после M3, перед M4** (решение 2026-09-08):
      прототип — один X-API-Key + матрица ролей в docs (`docs/security.md §6`);
      подход — демонстрация «без защиты → с защитой» (prompt-injection/утечка промпта +
      токсичный вывод на abliterated-LLM; guardrails inbound/outbound; hardening-документация,
      `docs/security.md §6.1`).