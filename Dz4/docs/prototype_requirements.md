# Документация: Требования к прототипу (Prototype Requirements)

> **Версия:** v1 (стартовая редакция)
> **Последнее обновление:** 2026-09-05
>
> **Corrective notice (2026-09-25):** typed ontology, runtime `_validate_ontology`,
> `ensure_schema` и Neo4j constraints из старой линии superseded change
> `add-lightweight-context-graph` / ADR-031. Primitive ingest и optional dynamic context graph
> являются актуальным контрактом; исторические typed-задачи не закрывают текущий DoD.

## 1. Цель прототипа

**Проверить на практике утверждения технической документации (v5–v7):**

1. Что vector-only baseline с metadata даёт работоспособное grounding и retrieval без graph.
2. Что optional graph experiment, построенный inline или offline, даёт измеримый прирост
   контекста по сравнению с baseline (ADR-015, метрики recall/coverage/groundedness).
3. Что ядро остаётся domain-agnostic: primitive ingest не требует фиксированной бизнес-онтологии,
   а Domain Profile даёт optional hints и изоляцию tag cloud.
4. Что слой адаптеров изолирует ядро от конкретных технологий: смена реализации
   (`graph_store`, `vector_store`, `llm`, `embeddings`, `reranker`) через runtime config
   не требует правки ядра (L1-02, L1-03).
5. Что пайплайн из обязательных document/chunk/embed/vector-commit этапов и optional enrichment
   реально исполним на целевом железе; graph не является тяжёлой ontology-зависимостью.
6. Что sparse graph expansion после vector search даёт измеримый прирост контекста при
   сохранении vector-only baseline и одинаковой revision.

Прототип — **не цель, а валидация**: любые расхождения с документацией фиксируются и
возвращаются либо в реализацию, либо в ADR (запись нового решения).

## 2. Границы (что НЕ входит в прототип)

| Исключено | Причина | Куда уходит |
|-----------|---------|-------------|
| Каскадная очередь ingestion (Kafka/RabbitMQ) | Прототип — синхронная схема; очередь — фаза роста | `docs/06`, `infrastructure_stack` §3 |
| Полноценные outbox, dual-generation и автоматическая миграция graph/vector-проекций | Не нужны для измерения вклада графа; offline rebuild разрешён как отдельная idempotent job | **M6-Growth / pre-connectors**, `docs/06` §4 |
| Reconciliation с TMS/GitLab | Операционная задача, не критична для валидации | `operations_requirements.md` §3 |
| Eval-инфраструктура с LLM-as-judge | Метрики groundedness требуют доработки judge-шага | ADR-015 §5, `operations_requirements.md` §5 |
| Multi-GPU / кластеры (K3s/K8s, Qdrant-кластер, Memgraph) | Фаза «Рост», после прототипа | `infrastructure_stack` §3 |
| Web UI конфигуратор / Topology UI | Нужен для UX-валидации лишь на поздних вехах; для API-валидации достаточно curl/WebSocket-клиента. Topology UI — отдельное приложение по ADR-019 | `docs/04` §1, CONCEPT §6.4, `docs/05_adr_log.md` ADR-019 |
| MCP-шлюз | ADR-017 зафиксирован, но интеграция с ИИ-агентами — отдельная веха | ADR-017 |

Скоуп формируется по принципу «минимально достаточной демонстрации» каждого утверждения
из §1. Не входящее в скоуп не реализуется и не тестируется на этом этапе.

### 2.1. Допущения прототипа (ограничения среды, не платформенные инварианты)

Следующие решения приняты для прототипа как демонстрационные допущения и **не переносятся
в продакшен без пересмотра**:

| Допущение | Суть | Снимается | Путь в продакшен |
|-----------|------|-----------|-------------------|
| `INGEST_MAX_CONCURRENT=1` | Сериализация ingestion-джоб для обхода Neo4j deadlock при параллельной записи в общие сущности (ADR-027-паттерн: ограничение среды, не инвариант) | **Снято 2026-09-22** (ретракт к 2 после S2-приёмки, ADR-028: retry + детерминированный порядок) | Очередь ingestion (Redis/RabbitMQ → Kafka, `docs/06` §4) |
| Retry transient-ошибок COMMIT | Повтор транзакции при `TransientError` (deadlock/сеть) с backoff+jitter — демонстрация resilience, не боевой механизм | — (переносится, но с ограничениями: max retry, без 2PC) | Воркеры очереди с retry-политикой на уровне worker |
| Детерминированный порядок записи | Сортировка `nodes`/`edges` по `node_id` перед MERGE — снижает deadlock, не исключает 100% | — (переносится как контрактная гарантия) | Та же (порядок фиксируется контрактом) |
| bge-m3 + Qwen 2.5 Coder 7B Abliterate (GPU) | Гейтинг L4-01, ограничение среды (ADR-027) | Стадия «Рост» (GPU 2, вынос LLM) | `docs/06` §4, ADR-027 |

> **Важно:** эти допущения зафиксированы как ограничения среды прототипа, а не платформенные
> инварианты (ADR-027-паттерн). Инварианты L1–L5 остаются в силе; допущения снимаются
> при масштабировании или фиксируются в ADR (retry transient → ADR-028, ordering → ADR-028).

## 3. Системные требования

### 3.1. Минимальное окружение (целевое)

| Ресурс | Требование | Примечание |
|--------|-----------|------------|
| ОС | Linux (Ubuntu 22.04+), WSL2 (Windows 11), macOS (M1/M2/M3) | — |
| CPU | 4 ядра (мин.), 8 ядер (реком.) | Реранкинг bge-reranker-base на CPU |
| RAM | 16 ГБ | Neo4j ограничен 1.5 ГБ JVM |
| GPU | NVIDIA, 8 ГБ VRAM (RTX 2070 Super и выше) | bge-m3 + Qwen 2.5 Coder 7B Abliterate поочерёдно (гейтинг L4-01, ограничение среды — ADR-027) |
| Диск | ~20 ГБ свободного (веса моделей + индексы) | Volumes сохраняются между запусками |
| Docker | Engine v24.0+, Compose v2.20+ | — |
| CUDA | Поддержка в Docker (`--gpus all` работоспособен) | Проверка — `docker run --rm --gpus all ...` |

### 3.2. Целевое железо прототипа (фиксированное)

- 1× NVIDIA RTX 2070 Super, 8 ГБ VRAM.
- bge-m3 и Qwen 2.5 Coder 7B Abliterate работают **поочерёдно** через compose-профили
  (`embeddings` / `llm`), ограничение среды прототипа — гейтинг L4-01 (ADR-027), риск №1 (`docs/06` §5).

## 4. Стек прототипа (выбранные backend'ы; не архитектурная привязка)

| Ось | Выбранная реализация | Интерфейс адаптера |
|-----|----------------------|--------------------|
| Граф | Neo4j Community (prototype default; optional generic expansion) | `GraphStoreProvider` |
| Векторы | выбранный vector backend; Neo4j native index в prototype | `VectorStoreProvider` |
| Конфиги/глоссарий | SQLite + YAML-профили | Config / Glossary Service |
| LLM | llama.cpp + Qwen 2.5 Coder 7B Abliterate q4_K_M (GGUF, `/v1`) | `LLMInference` |
| Embeddings | bge-m3, 1024 dim (Embeddings Service :8004 или LocalSentenceTransformerAdapter) | `Embedder` |
| Reranker | bge-reranker-base (CPU); может быть отключён (NoOpRerankerAdapter) | `Reranker` |
| Оркестрация | Docker Compose, сеть `ohw_net` | — |
| Очередь запросов | Valkey / Redis Streams (сетевой контур, v6) | — |
| Сетевой контур (Query API Gateway :8000, Query Workers) | **Python 3.11 + FastAPI** на прототипе (ADR-020); целевая замена на Go/Rust — фаза 2, процедура в `docs/web_layer_replacement.md` | — |
| Топология | Topology Orchestrator Service (:8005) + Topology UI (:8502) — отдельный сервис и приложение (ADR-019) | — |

Полная карта контейнеров, портов, профилей и лицензий — `docs/infrastructure_stack.md`.

Значения в таблице — выбранный backend прототипа, а не архитектурная зависимость:
graph и vector подключаются через независимые `GraphStoreProvider` и
`VectorStoreProvider`; они могут использовать разные хранилища, а Neo4j не является
обязательным.
Портовая карта конкретной инсталляции (`8000–8005`, `8501–8502`, `7687/7474`, `8080`) —
`docs/04_services_config.md` §1; консервированный снапшот конкретики v5 (до агностификации
Этапа 7) восстановим из git-истории — см. `docs/history.md` Этап 7.

**Структура каталога прототипа** (артефакты живут отдельно от `docs/`):

```
Dz4/
├── .opencode/                      # harness: скиллы openspec-propose/apply, агент reviewer
├── openspec/
│   ├── project.md                  # паспорт проекта (стек, конвенции)
│   └── changes/add-prototype-m0-services/   # бандл вехи M0 (proposal/design/tasks/spec)
├── docs/                           # живая документация (SSOT)
└── prototype/
    ├── infra/
    │   ├── compose.yaml            # Docker Compose (профили, ohw_net, JVM-лимиты)
    │   ├── config/
    │   │   ├── adapters.yaml       # селектор адаптеров (namespace: adapters)
    │   │   └── namespaces.yaml     # дефолты всех namespace Config Service
    │   └── eval/{it,library,cinema}/   # questions.jsonl (разметка на этапе M4)
    ├── domain_profiles/
    │   ├── domain_profile.{it,library,cinema}.yaml
    │   └── glossary.{it,library,cinema}.yaml
    ├── infra_topology.yaml         # топология инсталляции (ADR-019)
    ├── src/                        # Python-код (каркас по вехам M0–M4)
    └── pyproject.toml, Dockerfile  # uv-проект и образ сервисов (веха M0)
```

Размещение в `prototype/` (а не в корне рядом с `docs/`) отделяет временный
валидационный контур от стабильной документации; монтаж путей в compose —
относительно корня `prototype/`.

## 5. Зафиксированные контракты (исполнять как есть)

Прототип обязан реализовать утверждённые контракты без их изменения (см. ADR):

| Контракт | Фиксация | Ключевые точки |
|----------|----------|----------------|
| Асинхронный Query API «202 + task_id + стриминг» | `api_reference.md` §3, ADR-016 | POST /query, status/`token`/`done`/`error` |
| JSON-Schema конверта события | ADR-016 | `{type, task_id, ts, payload}`, SSE/WS |
| MCP-инструменты | ADR-017 | graphrag.query/get_active_domain/resolve_term/get_sources |
| Ingestion API + жизнь джобы | ADR-018, ADR-014 | POST /documents, GET/DELETE /jobs/{id}, lifecycle |
| Glossary API | ADR-018 | GET {domain}, RESOLVE, VALIDATE |
| Config API / Runtime | `docs/04` §2–§3 | domain validate/activate, adapters PUT |
| Topology API / Topology UI | ADR-019 | Topology Orchestrator Service :8005, Topology UI :8502 (оператор) |
| Жизненный цикл источника | ADR-014 | идемпотентность по content hash, soft-delete, версии |
| Инварианты L1–L5 | `docs/invariants.md` | обязательный чек-лист при ревью прототипа |

**Контракты стриминга имеют статус `v1-draft`** (ADR-016): в ходе прототипирования допустимы
мелкие правки полей, фиксируемые ADR-записью, до выпуска v1.

## 6. Состав работ по вехам

> **Статус реализации (обновлено 2026-09-10):**
>
> | Веха | Статус | Коммиты | Кратко |
> |------|--------|---------|--------|
> | M0 — Инфраструктура | ✅ закрыта | `9225b3a` | Compose+профили, Config :8001, Glossary :8003, Neo4j+llama.cpp |
> | M1 — Ingestion Pipeline | ✅ закрыта | `55914ff` | Ingestion API :8002, primitive-этапы INGEST→COMMIT, DocumentReader (ADR-021) |
> | M2 — Query API async + Retriever | ✅ закрыта | `a9c36f3`, `b7558ca`, `93b96c3` | Query :8000+SSE, Worker+Valkey (ADR-023), vector-first ретривер, demo-ui+e2e, тумблер граф-оси |
> | M3 — Адаптеры и runtime-переключение | 🚧 в работе | `dfb7e9f`, `81490af`, `0208c28` | Бандлы 2/3 и 3/3 реализованы и закоммичены: `add-real-embeddings-reranker` (Embeddings :8004 bge-m3, Reranker :8006 bge-reranker-base, адаптеры, EmbedStage на Embedder, live mock + real-прогон MiniLM/CE) и `add-semantic-cache` (Valkey-кэш семантических ответов, hit/miss + `cache_hit`/`cache_lookup_s`; A-2 — честный контракт атомарности COMMIT) |
> | M4 — Eval и гейт готовности | ⬜ не начата | — | — |
> | M5 — MCP-шлюз и UI | ⬜ не начата | — | — |
> | M6 — Внешние источники (коннекторы) | ⬜ запланирована | — | План-бандл `add-source-connectors`; гайд `docs/connectors_guide.md`; реализация — по потребности, вне скоупа M3–M5 |
>
> Детали каждого бандла — в `docs/history.md` «Этап 9» и `openspec/changes/<bundle>/`.
> Отдельные пункты вех, помеченные ниже как «(partial)», реализованы частично
> (например, EMBED до M3 — детерминированный эмбеддер вместо bge-m3).

### Веха 0 — Инфраструктура (основа)
- [x] Docker Compose: профили `config`, `graph` (Neo4j, ограничение JVM), сеть `ohw_net`.
- [x] Проверка GPU через Docker, каталог volumes для весов моделей.
- [x] Config Service: SQLite + загрузка Domain Profile (YAML), endpoints `docs/04` §2.
- [x] Glossary Service: `glossary.{profile}.yaml`, RESOLVE/VALIDATE (стек proto: SQLite).
- [x] Topology Orchestrator Service (:8005) + профиль `topology` (ADR-019).

### Веха 1 — Ingestion Pipeline (primitive-этапы INGEST→COMMIT)
- [x] Ingestion API (:8002): POST /documents, GET/DELETE /jobs/{id} (ADR-018).
- [x] Этап INGEST→COMMIT: CHUNK (512/64), EMBED (bge-m3, batch 32; на прототипе до M3 —
      детерминированный эмбеддер, см. add-real-embeddings-reranker), EXTRACT (Qwen),
      NORMALIZE (v3, fallback), DEDUP (0.92/0.75/0.85), CONTRACT, VALIDATE, COMMIT.
- [x] Document Registry + версии источника (ADR-014), идемпотентность по content hash.
- [x] Семейство «запуск профилей embeddings/ingestion поочерёдно» (гейтинг L4-01, ограничение
      среды — ADR-027): процедура
      фазирования — `docs/demo_runbook.md` «GPU-гейтинг»; реальный VRAM-прогон с
      bge-m3 :8004 — в бандле 2/3 add-real-embeddings-reranker.

### Веха 2 — Query API (асинхронный контур)
- [x] Query API (:8000): POST /query → 202, GET /query/tasks/{task_id}.
- [x] Query Workers + Task Queue (Valkey/Redis Streams).
- [x] Стриминг WebSockets/SSE: контракт ADR-016.
- [x] Retriever: vector-first (Vector + Reranker), окно контекста 4096 с вытеснением; graph experiment опционально, выполняется после vector search.

### Веха 3 — Адаптеры и runtime-переключение
- [x] Интерфейсы-адаптеры: GraphStoreProvider, VectorStoreProvider, LLMInference, Embedder, Reranker.
- [~] Реализации-кандидаты из снапшота v7 (§2): [x] Neo4jGraphStore, [x] Neo4jVectorStore,
      [~] OpenAICompatibleAdapter (вместо OllamaAdapter — подключается бандлом LLM-режима),
      [x] BgeM3ServiceAdapter (:8004), [x] BgeRerankerAdapter (:8006) — бандл 2/3
      add-real-embeddings-reranker (реальные эмбеддинги + реранкер, mock/real-режимы).
- [x] `PUT /api/v1/config/adapters` — переключение оси на лету через Topology Orchestrator
      Service (:8005) без перезапуска (L1-03, ADR-019) + hot-reload воркера.
- [x] Несколько профилей доменов (it / library / cinema) + переключение активацией (L1-01):
      активация через `POST /domain/activate` (Config :8001); Glossary и Query-сервис
      тянут активный домен pull-моделью на каждый запрос — переключение без рестарта
      (`docs/demo_runbook.md`, `tests/test_domain_activation.py`).

### Веха 3-хвосты — чанкинг: ABC, настройки, структура, внешние сплиттеры, native index
- [x] `Chunker` ABC + `SlidingWindowChunker`: вынос `_sliding_window` (512/64) из оркестратора
      в адаптер, `ChunkStage` на DI (как `EmbedStage` ← `Embedder`); поведение по умолчанию — то же.
- [x] Настройка чанков: per-field precedence `INGEST_CHUNKER`/`INGEST_CHUNK_SIZE`/`INGEST_CHUNK_OVERLAP`
      (env) > профиль домена (секция `chunking`, per-job из Config Service) > namespace `chunking`
      в `infra/config/namespaces.yaml` > дефолты M1 (512/64); `build_chunker_for(domain)`,
      профиль недоступен → fallback (ingest не падает).
- [x] `StructureAwareChunker`: пер-секционный чанкинг по заголовкам Markdown (`^#{1,6}\s`),
      короткая секция → один чанк; для текста без структуры (.txt) — fallback на sliding window.
- [x] LangChain/LlamaIndex chunker-адаптеры: optional deps (`pyproject [chunking]`), lazy import
      (как torch/sentence-transformers), fail-fast при отсутствии пакета.
- [x] Entry-point плагины стратегий: generic-реестр `graphrag_proto/plugin_registry.py`
      (discovery `importlib.metadata`), группа `graphrag.chunkers`, `list_chunkers()`,
      приоритет встроенных, fail-fast без тихого fallback (бандл add-chunker-entry-points).
- [ ] Native vector index (Neo4j HNSW `db.index.vector.queryNodes`) вместо Cypher cosine — низкий
      приоритет, требуется при росте числа чанков (ADR-023 OQ2); реализация внутри `Neo4jVectorStore`,
      ядро (ABC) не меняется.

### Веха 3-хвосты — Semantic Cache (бандл 3/3, M3.3)
- [x] `SemanticCache` ABC + `CachedAnswer`; `InMemorySemanticCache` (cos-порог, TTL, `stats()`, `clear()`).
- [x] `RedisSemanticCache`: HASH `query:sc:<domain>`, поле `sc:<sha256(repr(embedding))>` (полный digest, обновлено 2026-09-14), JSON
      `{embedding,text,sources,ts}`, lazy-`redis`, TTL-чистка HDEL (ADR-025).
- [x] QueryPipeline: `semantic_cache` параметр (None = выкл, поведение M2); hit → `cache{hit:true}` +
      `done(cache_hit:true, token нет, LLM не вызывался)`; miss → полный цикл + запись в кэш.
- [x] Env-конфигурация: `SEMANTIC_CACHE_ENABLED/MODE/THRESHOLD/TTL_S`, `QUERY_REDIS_URL`; сборка
      один раз в `worker.main()` — переживает hot-reload адаптеров (топология).
- [x] «Плохие» ответы не кэшируются (`should_cache_text`: пустой `text`, отказ LLM «контекста
      недостаточно»); допущение инвалидации TTL+`clear()`, planned upgrade — epoch-bump
      `query:sc:<rev>:<domain>` (Веха 4-хвост), зафиксировано ADR-025.
- [x] Тесты: `tests/test_semantic_cache.py`, кэш-hit/miss в `test_retrieval_pipeline.py`,
      hot-reload-preserve в `test_worker_hotreload.py`; коммит `0208c28`.

### Веха 4 — Eval и гейт готовности
- [x] Eval-датасет `prototype/infra/eval/{domain}/questions.jsonl` (50 вопросов/it, 10/library, 10/cinema)
      — валидация через `tests/test_eval_dataset.py`, формат ADR-015 (id, query, golden_sources, golden_facts, category).
- [x] Метрики Retrieval@K=5 (Recall, Precision, MRR, nDCG), generation (groundedness, coverage,
      hallucination_rate) — `src/graphrag_proto/eval/metrics.py`, 10 unit-тестов (`test_eval_metrics.py`).
- [x] Eval-раннер `src/graphrag_proto/eval/run_eval.py` — CLI `run_eval.py --domain --mode --questions`,
      агрегация метрик, lift-отчёт JSON+markdown.
- [x] Контрактный тест `tests/test_llm_openai_adapter.py` — streaming, non-streaming, HTTP 500,
      timeout, connection refused; 5 тестов через stub HTTP-сервер.
- [x] Baseline (vector-only) vs target (hybrid) — критерий готовности прототипа (§8).

### Веха 4-хвост — Ревизия данных в query-контуре (отдельное решение; обязательна ДО M5/M6)

Решение, отложенное на обсуждении свежести семантического кэша (2026-09-13). Суть — не
«инвалидация кэша», а экспорт уже существующих версий в query-контур, чтобы у «актуального
контекста ответа» появился fingerprint. Взаимодействие с концепцией: L2-07 получает явный
**bounded staleness** (окно согласования ingestion→query), ADR-014/topology-revision
экспортируются (без параллельной модели ревизии), Eval (ADR-015) фиксирует ревизию знаний
в срезе. До появления вехи кэш живёт на TTL + ручной сброс (допущение зафиксировано в
`openspec/changes/add-semantic-cache/`).

**Решение принято 2026-09-14** (зафиксировано ADR-026, аналитика — `analitic/revision/data_revision_analytics.md`):

- **Гранулярность — по-доменная** (`rev<domain>`). Кэш, профили, ingestion и Eval уже
  по-доменные; мультидоменная инвалидация изолирована (переиндексация `it` не сжигает
  кэш `legal`).
- **Источник rev — fingerprint активного сета DocumentRegistry**:
  `rev<domain> = sha256(sorted((source_url, content_hash) активных документов домена))`.
  Идемпотентен (no-op INGEST не меняет), честно отражает коллекцию источников
  (2 книги EN+RU на тему = 2 `content_hash` в множестве), переиспользует поля
  ADR-014 — без новой параллельной модели ревизии.
- **Доставка — `GET /api/v1/ingestion/revision?domain=` + поллер в worker**
  (по аналогии с `_topology_rebuilder`, задержка = интервал полла).

- [x] Экспонировать версии DocumentRegistry (ADR-014) + topology-revision в query-контур;
      fingerprint «профиль домена + карта адаптеров + версия данных» — переиспользование
      ADR-014/topology, без новой параллельной модели.
- [x] Реализовать `DocumentRegistry.data_revision(domain) -> str | None` (sha256 активных
      пар `(source_url, content_hash)`) + endpoint `GET /api/v1/ingestion/revision?domain=` (X-API-Key).
- [x] Query-контур: поллер ревизии (аналог `_topology_rebuilder`), текущая `rev<domain>`
      в lookup/store; кэш-объект один (ADR-025), старые эпохи умирают по TTL.
- [x] Семантический кэш: ключ `query:sc:<rev>:<domain>` (epoch-bump префикс в поле HASH).
- [x] `docs/invariants.md` L2-07: зафиксировать bounded staleness явно
      (текущая трактовка подразумевает «свежесть ансвера = свежесть данных»).
- [ ] M4-Eval: воспроизводимый срез фиксирует ревизию знаний (ADR-015).

### Веха 5 (опционально/отдельным решением) — MCP-шлюз и UI

> Предусловие: веха 4-хвост «Ревизия данных в query-контуре» (конфигуратор профилей
> меняет онтологию онлайн → нужен fingerprint контекста до/в паре с инвалидацией кэша).
> До M6-Growth онлайн-изменение ingestion-профиля не считается автоматической
> переиндексацией; для эксперимента профиль фиксируется на время корпуса.

- [ ] MCP-шлюз (:8000, JSON-RPC), инструменты ADR-017, rate-limiting (`docs/security.md` §4).
- [ ] Streamlit — конфигуратор «Бизнес-онтология» (:8501) — отдельное приложение.
- [ ] Streamlit — Topology UI «Топология инфраструктуры» (:8502) — отдельное приложение (ADR-019).

### Отложено до завершения проекта — OCR
- [ ] OCR: распознавание image-блоков (image → text) — новая реализация `DocumentReader`
      (ADR-021 family, пайплайн не переписывается) или OCR-ридер; для demo не требуется,
      поэтому перенесено к завершению проекта (после M4/M5).

### Веха 6 — Внешние источники (коннекторы) — запланирована (по потребности, вне M3–M5)

> Предусловие: веха 4-хвост «Ревизия данных в query-контуре» — потоковые источники
> делают окно «≤ TTL» бизнес-критичным; fingerprint данных обязателен до коннекторов.

> План-бандл: `openspec/changes/add-source-connectors/`. Гайд по созданию коннекторов —
> `docs/connectors_guide.md`: точка подключения — ДО пайплайна (материал → ридер → канонический
> документ, ADR-021), ядро не переписывается.

- [ ] Коннекторы внешних систем (Jira, TestRail/Test Management, Confluence/Wiki, GitLab)
      поверх их API — по шагам гайда; в ядро коннекторы не попадают.
- [ ] Интерфейс `SourceProvider` (`fetch(source, creds) -> bytes`) + реестр коннекторов по `doc_type`.
- [ ] (Опционально) слот `source` в топологии — переключение активных источников на лету,
      по аналогии с адаптерами M3.
- [ ] Реализация не меняет push-контракт `POST /documents` (ADR-018) и пайплайн (ADR-021).

### M6-Growth / pre-connectors — lifecycle проекций (отложено)

- [ ] Ввести outbox для событий перестроения graph/vector-проекций.
- [ ] Разделить `vector_fingerprint` и `graph_fingerprint`; no-op считать отдельно для
  каждой проекции.
- [ ] Реализовать dual-generation: новая generation строится и проверяется рядом со старой,
  затем публикуется active manifest/pointer; старые generation удаляются после drain.
- [ ] Обрабатывать смену профиля/онтологии/chunker/embedding без привязки к Neo4j;
  `GraphStoreProvider` и `VectorStoreProvider` остаются единственными backend-контрактами.
- [ ] До этой стадии профиль и pipeline-контракт загруженного корпуса считаются неизменными:
  автоматический reindex после смены профиля не поддерживается, а чистый rebuild является
  ручной операцией. Это ограничение не относится к замеру baseline/target.

Стадия не является предусловием M4-eval gate и не блокирует текущий прототип.

## 7. Валидационные проверки по инвариантам

| Инвариант | Проверка на прототипе |
|-----------|-----------------------|
| L1-01 домен-агностичность | Активация it → library → cinema без изменений кода |
| L1-02/L1-03 интерфейсы и runtime config | Смена `vector_store` на Qdrant / `llm` на OpenAICompatible — работает без правки ядра |
| L1-05 детерминизм | Повторный прогон NORMALIZE/DEDUP даёт тот же результат |
| L2-01 canonical_name | контекстный тег, не глобальный constraint; разрешение через tag_id/aliases |
| L2-04 атомарность COMMIT | Атомарная пара (оба `atomic` + общий движок) — обе оси в одной транзакции: ошибка на COMMIT → граф без частичных записей; разнородная пара — best-effort: сбой второй оси → граф компенсирован (`delete_node`), джоба `failed` с пометкой «компенсировано» (ADR-024) |
| L3-01..L3-05 | Выполнение пайплайна и контрактов (журнал этапов, лимит контекста, 202+task_id) |
| L4-01 гейтинг (ограничение среды прототипа, ADR-027) | Профили embeddings/ingestion не конфликтуют по VRAM |
| L5-01..L5-04 | X-API-Key во всех контурах; redaction секретов; 401 на неверный ключ |

## 8. Критерий готовности прототипа (объявляется по результатам)

Прототип считается **валидированным**, когда:

1. Пайплайн исполним на целевом железе без ручного вмешательства (один вызов Ingestion API
   проходит обязательные document/chunk/embed/vector-commit этапы).
2. Query API: вопрос через асинхронный контур возвращает ответ + sources + тайминги
   (стриминг по контракту ADR-016).
3. Переключение Domain Profile работает без перезапуска контейнеров.
4. **Eval-гейт:** groundedness, coverage и hallucination_rate не ниже baseline (vector-only) или приращение
   задержке (ADR-015, «валютное» правило). Если гибрид не даёт прироста — фиксируется ADR
   о пересмотре подхода (это легитимный результат валидации).
5. Все пункты вех M0–M4 (без M5) отмечены выполненными.

После объявления готовности — переход к «фазе 2» (UI конфигуратор/Topology UI, MCP, очереди,
продакшен-операции) и заполнение User Guide / Runbook (разделы этого документа).

---

## Приложение A: User Guide (заполняется после реализации)

> Роли и контракты зафиксированы в `docs/security.md` §1 и `docs/api_reference.md`.
> Конкретика UX (скрины, шаги в UI) пишется после сборки прототипа.

### A.1 Роли и что они могут
| Роль | Возможности | Поверхность |
|------|-------------|-------------|
| Analyst | Задаёт RAG-вопросы, смотрит источники и тайминги | Query API / Query-клиент |
| Configurator | Редактирует и активирует Domain Profiles, глоссарии | Config/Glossary API / конфигуратор :8501 |
| Ingestor | Загружает и удаляет источники | Ingestion API |
| Operator | Настраивает топологию, адаптеры, мониторинг | Topology UI :8502 / Topology API :8005 (ADR-019) |

### A.2 Типовой сценарий аналитика
1. `POST /query` с вопросом (X-API-Key) → 202 + `task_id`.
2. Подписка на стриминг (WS/SSE), получение `token`/`done`.
3. По `done` — текст ответа, `sources` (source_url + relevance), `generation_time_s`.

### A.3 Типовой сценарий конфигуратора
1. `POST /api/v1/config/domain/validate` — валидация профиля (вкл. глоссарий).
2. `POST /api/v1/config/domain/activate` — активация.
3. Glossary: RESOLVE (тег → canonical_name), VALIDATE уникальности.

### A.4 Типовой сценарий оператора (Topology, ADR-019)
1. Вход в Topology UI (:8502) с ключом оператора.
2. Правка `prototype/infra_topology.yaml`: выбор драйверов (GraphStore, VectorStore, LLM, Embeddings).
3. Применение через Topology Orchestrator Service (:8005) — адаптеры переключаются без
   перезапуска контейнеров (L1-03).

## Приложение B: Runbook (заполняется после сборки прототипа)

> Симптом → причина → фикс. Пока пустой; заносится в ходе М0–М4.
> Стартовые ориентиры: README §3 (запуск), §5 (Troubleshooting), `prototype/infra/compose.yaml`
> (параметры JVM/профили), `docker logs` по контейнерам.

| Симптом | Причина | Фикс |
|---------|---------|------|
| (заполняется) | | |

## Связи

| Тема | Документ |
|------|----------|
| Материал конкретики v5 (реализации, порты, тайминги) | git-история; `docs/history.md` Этап 7 |
| Стек, порты, профили, модели | `docs/infrastructure_stack.md`, `docs/04_services_config.md` |
| Контракты API и события | `docs/api_reference.md`, ADR-016/017/018 |
| Инварианты и чек-лист ревью | `docs/invariants.md` |
| Eval-методология | `docs/05_adr_log.md` ADR-015 |
| Запуск/остановка, системные требования | корневой `README.md` §1, §3, §5 |
| Эксплуатация (вне прототипа) | `docs/operations_requirements.md` |
