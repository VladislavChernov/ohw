# История разработки концепции GraphRAG

> **Версия:** v14 (архивный срез `docs.zip` снят с дерева — восстановим из коммита `8a83c72`; реализация прототипа: вехи M0–M2, M3-бандлы адаптеров+topology, embeddings+reranker, semantic-cache; M3-хвосты чанкинга+плагинов; self-contained LLM-образ; X-API-Key на всех HTTP-контурах; SQLite WAL+busy_timeout; лимит параллельных джоб ingestion c 429; единый словарь id адаптеров YAML↔фабрика; redaction секретов L5-02; CI GitHub Actions; отказоустойчивость query-контура: reclaim PEL + бэкофф воркера, SSE heartbeat, общий executor пайплайна; A-2: честный контракт атомарности COMMIT — capability derivation atomic/best_effort + компенсация, ADR-024; Semantic Cache на Valkey, ADR-025; решения по кэшу и ревизии данных; P0-закрытие ревью: fail-open кэша, clear() SCAN+DEL, stats() HLEN, EXPIRE на HASH=TTL, полный sha256-ключ; L4-04 этап A: trigger_metrics snapshot + счётчик ошибок поллера топологии; ADR-027: L4-01 переквалифицирован — гейтинг = ограничение среды прототипа, инвариант = контракт LLMInference; S2 fencing query-воркеров: worker_id в ack/fail (InMemory ownership, Redis PEL), skip claim уже-терминальной задачи, commit-идемпотентность mark_succeeded; блокеры ревью №7: socket_timeout/connect_timeout у клиента Redis-очереди, LLM_TIMEOUT_S в фабрике адаптеров, shared-счётчики hit/miss кэша в Valkey (HINCRBY query:sc:<domain>:meta); M4 eval-инфраструктура (ADR-015): метрики + датасеты + раннер; ADR-028: политика конкурентной записи COMMIT — retry transient + детерминированный порядок (S1-обход M5 `INGEST_MAX_CONCURRENT=1` → S2 закрыт код+тесты+доки, L3-06))
> **Последнее обновление:** 2026-09-19

Этот документ содержит исторические материалы, отражающие этапы развития концепции GraphRAG платформы.

---

## Этап 1: Первоначальная концепция (summary.md)

**Дата:** 04.09.2026  
**Версия:** 2.0  
**Статус:** Архив

Эта версия представляет собой первоначальное описание подхода Hybrid GraphRAG для ИТ-проектов.

### Ключевые идеи:

1. **Гибридный подход:** Синергия векторного RAG и графа знаний
2. **Тройная база знаний:** Интеграция книг по алгоритмам с ИТ-документацией
3. **Этапы разработки:** Чётко очерченный путь от прототипа до production-ready системы
4. **Метрики и оценка:** План измерений для доказательства эффективности графового подхода

Эта версия сфокусирована на ИТ-домене и не включает доменно-агностичную архитектуру, которая была добавлена в v4.

---

## Этап 2: Расширенная концепция (набросок системы.md)

**Дата:** 04.09.2026  
**Версия:** Полный черновик  
**Статус:** Архив

### Ключевые дополнения:

1. **Мультидоменность:** Идея поддержки разных предметных областей (ИТ, литература, кино)
2. **Мультиязычность:** Обработка RU/EN контента с англоязычным ядром графа
3. **Изоляция загрузки данных:** Чёткое разделение ingestion и retrieval процессов
4. **API спецификация:** Подробное описание эндпоинтов и форматов запросов/ответов
5. **Матрица рисков:** Анализ критических проблем и стратегий их устранения

---

## Этап 3: Формализация v3 (concept_v3.md)

**Дата:** 04.09.2026  
**Версия:** 3  
**Статус:** Архив

### Ключевые изменения:

1. **Структурированная онтология:** Чёткое описание типов узлов и связей
2. **ADR (Architecture Decision Records):** Формализация архитектурных решений
3. **Техническая спецификация:** Подробные секции по каждому компоненту
4. **Система валидации:** Валидатор графа с Cypher-правилами

---

## Этап 4: Доменно-агностичная архитектура v4 (concept_v4.md → CONCEPT.md)

**Дата:** 04.09.2026  
**Версия:** 4  
**Статус:** Активная

### Ключевые нововведения:

1. **Domain Profile (YAML):** Вынос всей доменной логики в конфигурацию
2. **Поддержка нескольких доменов:** IT, Literature, Cinema и др.
3. **Динамическая конфигурация:** Runtime-переключение доменов через API
4. **Расширенная валидация:** Cypher-правила из доменного профиля
5. **Стратегии канонизации:** Контекстно-зависимая обработка разных типов сущностей

### Архитектурные принципы v4:

- **Ядро фиксировано:** Ingestion Pipeline, Retriever, Services, Storage, Observability
- **Доменная специфика конфигурируется:** Ontology, Prompts, Validation Rules, Glossary, Chunking Strategy
- **Ноль кода для нового домена:** Только YAML + поддержка в глоссарии
- **Production-ready:** Поддержка масштабирования, наблюдаемости, управления рисками

---

## Сравнение версий

| Функция                          | v2 (summary) | v3 (concept_v3) | v4 (concept_v4) |
|----------------------------------|--------------|-----------------|-----------------|
| Гибридный подход (Graph + Vector)| ✅          | ✅             | ✅             |
| Мультидоменность                 | ❌          | ❌             | ✅             |
| Domain Profile (YAML)            | ❌          | ❌             | ✅             |
| Runtime API для переключения     | ❌          | ❌             | ✅             |
| ADR                              | ❌          | ✅             | ✅             |
| Система валидации                | ✅          | ✅             | ✅             |
| Мультиязычность                  | ❌          | ❌             | ✅             |

---

## Этап 5: Слой адаптеров v5 (CONCEPT.md v5.0)

**Дата:** 04.09.2026  
**Версия:** 5  
**Статус:** Активная

### Ключевые нововведения:

1. **Adapter Layer:** Слой абстракции, изолирующий ядро от конкретных технологий (Neo4j, Ollama, bge-m3, bge-reranker)
2. **5 программных интерфейсов:** GraphStoreProvider, VectorStoreProvider, LLMInference, Embedder, Reranker (изначально — 4: GraphStorage, LLMInference, Embedder, Reranker; разделение хранилища на граф/вектор зафиксировано в ADR-013)
3. **Runtime Config для адаптеров:** Переключение инфраструктуры через `namespace: adapters` без перезапуска
4. **Альтернативные реализации:** Qdrant, Memgraph, vLLM, OpenAI-совместимые серверы, sentence-transformers, NoOp
5. **Регистрация сторонних адаптеров:** Через `entry_points` (Python setuptools) без изменения кода ядра
6. **Mitigation Vendor Lock-in:** Замена любого инфраструктурного компонента — правка одного конфига

### Архитектурные принципы v5:

- **Адаптеры изолируют инфраструктуру:** Ядро не знает, какой конкретно эмбеддер, LLM, хранилище или реранкер используется
- **Runtime-переключение:** Замена адаптера — через Config Service API, без перезапуска контейнеров
- **Extensibility:** Сторонние разработчики могут создавать свои адаптеры через entry_points
- **Выбор стека под задачу:** Лёгкая инсталляция, замена хранилища, облачный LLM — без переписывания ядра
- **Разделение осей (ADR-013):** Граф и вектор — два независимых интерфейса (`GraphStoreProvider`/`VectorStoreProvider`), соединяющиеся только на Context Assembly

### Примеры адаптеров (после ADR-013):

| Адаптер           | Базовая реализация    | Альтернативы                                   |
|-------------------|-----------------------|------------------------------------------------|
| Graph (графовая ось) | Neo4jGraphStore      | Neo4jGrpcGraphStore, MemgraphGraphStore        |
| Vector (векторная ось) | Neo4jVectorStore    | QdrantVectorStore                              |
| LLM               | OllamaAdapter         | OpenAICompatibleAdapter, VllmAdapter           |
| Embeddings        | BgeM3ServiceAdapter   | LocalSentenceTransformerAdapter, OpenAIEmbeddingsAdapter |
| Reranker          | BgeRerankerAdapter    | NoOpRerankerAdapter, CohereRerankAdapter       |

---

## Этап 6: Асинхронная архитектура v6 (следующая итерация к v5)

**Дата:** 05.09.2026  
**Версия:** 6  
**Статус:** Обновление к актуальной v5 (не отдельная ветка)

### Ключевые нововведения:

1. **Асинхронная Task Queue:** Синхронный HTTP-контур Query API ликвидирован — запрос принимается мгновенно (`202 Accepted` + `task_id`), обработка через очередь (Valkey / Redis Streams) пулом Query Workers, доставка результата через WebSockets/SSE.
2. **Topology Orchestrator:** Фабрика провайдеров внутри Config Service — читает `infra_topology.yaml` и на лету подменяет InMemory-классы на сетевые драйверы (Redis/RabbitMQ/vLLM), изолируя бизнес-логику от инфраструктуры.
3. **ADR-013:** Разделение интерфейсов графового и векторного хранилищ (уточнение ADR-012) — `GraphStorage` аннулирован, введены `GraphStoreProvider`/`VectorStoreProvider`.
4. **C4 / arc42 паспорт:** Уровни 1–4 (системный контекст, контейнеры, GUI-конфигуратор, контракты gRPC/JSON-Schema).
5. **Мультиязычный контур:** Высоконагруженный сетевой контур (Query API Gateway, Query Workers, очередь) выносится на компилируемые языки (Go/Rust), ИИ-контур остаётся на Python.

### Архитектурные принципы v6:

- **Отказоустойчивость при пиках:** Веб-потоки Query API свободны; задачи разделяются через очередь.
- **Изоляция инфраструктуры:** Код бизнес-логики не знает деталей сетевой топологии (Topology Orchestrator).
- **Полиморфизм языков:** Go/Rust для сетевого слоя, Python для CUDA-вычислений, канонизации и Cypher-валидации.

### Отношение к v5:

v6 — следующая итерация концепции и документации поверх базы v5: SSOT-документы v5 (CONCEPT.md, `docs/`) остаются базой, актуальная картина архитектуры — `docs/00_hi_level_architecture.md` (v6). v6-специфичные артефакты — C4/arc42-паспорт уровня 1–4 (системный контекст; контейнеры с Task Queue / Query Workers / Topology Orchestrator; GUI-конфигуратор на 2 экрана; контракты gRPC/Proto3, JSON-Schema строки адресных контрактов Go/Rust-контура), ADR-013, Task Queue — в архивном срезе `v6/`, извлечённом из git-истории при необходимости.

---

## Этап 7: Агностификация CONCEPT v5.0 (итерация документации перед прототипом)

**Дата:** 2026-09-05  
**Версия:** 7  
**Статус:** Итерация документации поверх базы v5 (не отдельная ветка)

### Ключевые изменения:

`CONCEPT.md` приведён к **технологической-агностичности**: из спецификации вычищены прямые
привязки к инсталляции, которые теперь живут в справочниках `docs/` и в прототипе:

1. **Вычищено из §2 (адаптеры):** базовые реализации (Neo4jGraphStore, Neo4jVectorStore,
   MemgraphGraphStore, QdrantVectorStore, OllamaAdapter, OpenAICompatibleAdapter, VllmAdapter,
   BgeM3ServiceAdapter, LocalSentenceTransformerAdapter, OpenAIEmbeddingsAdapter,
   BgeRerankerAdapter, NoOpRerankerAdapter, CohereRerankAdapter) → `docs/adapters_specification.md`.
   Остались только интерфейсы GraphStoreProvider / VectorStoreProvider / LLMInference /
   Embedder / Reranker.
2. **Вычищено из §4–§5:** тайминги и имена моделей (bge-m3 ~50 мс, Qwen 3–10 сек), порты
   (:8002/:8004), параметры чанкинга и базовая реализация реранкера → `docs/02`, `docs/03`.
3. **Вычищено из §6.1:** карта портов и имя сети `ohw_net` → `docs/04 §1`,
   `docs/infrastructure_stack.md §2`. В CONCEPT — список сервисов без портов со ссылками.
4. **Вычищено из §6.5:** конкретные defaults namespace-ключей (включая `neo4j_uri`, имена моделей,
   `api_key`) → `docs/06 §1`. В CONCEPT осталась семантика namespace.
5. **Вычищено из §8:** стек метрик (PromQL), compose-профили, таблица масштабирования, матрица
   рисков → `docs/06 §2–§5`, `docs/infrastructure_stack.md §4`.
6. **Вычищено из §6.4/§2.5/§2.6:** имена глоссариев `glossary.{profile}.yaml`, curl-примеры на порт
   8001, пример `entry_points` → `docs/04`, `docs/adapters_guide.md`.

### Архитектурные принципы v7:

- **CONCEPT = инварианты:** интерфейсы, инварианты ядра, семантика namespace — без вендор-деталей.
- **Конкретика = справочники + прототип:** реализация живёт в `docs/` и в прототип-артефактах.

### Материал прототипа (архив):

Конкретика, вычищенная из CONCEPT, консервирована как **архивная итерация** (снапшот
`CONCEPT.md` v5.0 до чистки) и хранится в git-истории. Она служит материалом для сборки
прототипа и **не цитируется** в живых доках.

## Этап 8: Переход к прототипированию (согласование требований)

**Дата:** 2026-09-05  
**Версия:** 8  
**Статус:** Начата фаза прототипа (этап принятия решения)

### Ключевые изменения:

Верхнеуровневая техническая документация v5–v7 согласована: концепция агностифицирована
(Этап 7), добавлены ADR-014…018, security.md, operations_requirements.md и invariants.md.
Документация **не разрослась до подпапок** — структура каталога `docs/` осталась плоской
(SSOT-нумерация 00–06), логическая группировка вынесена в индекс `docs/README.md`.

Согласовано решение: следующим шагом является **сбор требований к прототипу**, а не
немедленная реализация кода.

### Артефакты:

- `docs/prototype_requirements.md` — цель/границы прототипа, системные требования, стек,
  зафиксированные контракты, вехи (M0–M5), валидация по инвариантам, критерий готовности
  (eval-гейт по ADR-015), заглушки User Guide и Runbook (приложения A/B).
- Решение «отложено» из `docs/operations_requirements.md` §6: runbook/запуск/системные
  требования перенесены в требования к прототипу.
- **ADR-019** — Topology Orchestrator выделен из Config Service в отдельный сервис (:8005)
  с собственным настроечным приложением Topology UI (:8502); экран «Топология инфраструктуры»
  вынесен из конфигуратора «Бизнес-онтология» (:8501). Обновлены порты, профили, роли,
  глоссарий, схема архитектуры.
- **ADR-020** — язык Query API Gateway (:8000) на этапе прототипа — Python 3.11 + FastAPI
  (гомогенный контур, скорость итераций; железо прототипа не ограничивает выбор). Сетевая
  граница объявлена заменяемой: смена языка (цель — Go/Rust, фаза 2) не затрагивает ядро,
  очередь и хранилища — процедура в `docs/web_layer_replacement.md`.

---

## Этап 9: Реализация прототипа — вехи M0–M2 закрыты, M3 в работе (бандл 2/3 реализован)

**Дата:** 2026-09-10
**Статус:** Активный код (прототип `prototype/`, репозиторий состоит из каталогов dz0–dz4)

Переход от требований (`docs/prototype_requirements.md`) к коду. Вехи M0–M2 завершены
и запушены в `origin master`; веха M3 ведётся бандлами OpenSpec
(`openspec/changes/`), каждый бандл — proposal → задачи → реализация → тесты → живой
прогон → ревью → коммит.

### M0 — Инфраструктура (коммит `9225b3a`)

- Docker Compose с профилями `config`/`graph`/`ingestion`/`llm` (Neo4j с ограничением JVM),
  сеть `ohw_net`.
- Config Service (:8001): SQLite + загрузка Domain Profile (YAML), runtime-namespaces
  (`docs/04` §2); Glossary Service (:8003): `glossary.{profile}.yaml`, RESOLVE/VALIDATE (ADR-018).
- Neo4j + llama.cpp (Qwen 2.5 Coder 7B Abliterate q4_K_M, ADR-022).

### M1 — Ingestion Pipeline, 9 этапов (коммит `55914ff`)

- Ingestion API (:8002): `POST /documents`, `GET`/`DELETE /jobs/{id}` (ADR-018).
- Конвейер INGEST→COMMIT: CHUNK (512/64), EMBED (на прототипе до M3 — детерминированный
  эмбеддер), EXTRACT (Qwen), NORMALIZE (v3 с fallback), DEDUP (0.92/0.75/0.85), CONTRACT,
  VALIDATE, COMMIT.
- DocumentReader + канонический формат документа (ADR-021); Document Registry с версиями
  источника (ADR-014) и идемпотентностью по content hash.

### M2 — Query API: асинхронный контур + Retriever (коммиты `a9c36f3`, `b7558ca`, `93b96c3`)

- Query API (:8000) async: `POST /query → 202` + `task_id`, `GET`/`DELETE /query/tasks/{id}`,
  SSE-стриминг (контракт ADR-016).
- Query Worker + Task Queue на Valkey/Redis Streams (ADR-023); отмена задач.
- Retriever: граф (Cypher) ∥ вектор + reranker (пока noop) + Context Assembly.
- Демо-контур (Streamlit :8500) + e2e-харнесс (`infra/scripts/run_demo_e2e.sh`).
- Тумблер графовой оси `RETRIEVAL_GRAPH_ENABLED` + тайминги `retrieval_time_s`/`total_time_s`
  (A/B на стеке: граф вкл 5.35 s vs выкл 0.75 s).

### M3 — бандлы (в работе)

**Бандл 1/3 «add-topology-adapters» — ЗАВЕРШЁН (коммиты `dfb7e9f`, `81490af`):**

1. **Каталог провайдеров + сборка по карте** — `retrieval/adapters/factory.py`:
   регистр слотов (`graph_store`, `vector_store`, `embeddings`, `reranker`, `llm`) и
   `build_adapters(adapter_map)`; приоритет слота **карта топологии > env > дефолт**;
   параметры соединений всегда из env. `TopologyClient` — тонкий HTTP-клиент
   (`adapters_map`/`revision`), сбои топологии → `None` (fallback на M2-поведение).
2. **Topology Orchestrator Service (:8005)** — реальный сервис вместо заглушки (ADR-019,
   профиль `topology`): читает `prototype/infra_topology.yaml`, отдаёт базовую карту
   адаптеров; `PUT /api/v1/config/adapters` пишет SQLite-override и увеличивает монотонный
   `revision` (только при фактическом изменении); `GET /api/v1/config/adapters/available` —
   только реализованные провайдеры. `X-API-Key` на всех эндпоинтах кроме `/health`.
3. **Hot-reload в Query Worker** — опрос `revision` интервалом `TOPOLOGY_POLL_INTERVAL`
   (дефолт 5 с, 0 — выключено); при смене пайплайн пересобирается **без рестарта
   контейнера**; топология недоступна — воркер работает по env как в M2.
4. **Live-приёмка** — GET/PUT адаптеров на живом стеке, revision растёт, в лог воркера
   падает «пересборка pipeline без рестарта», запрос проходит (succeeded); неизвестный
   провайдер → 422 без изменения revision. 144 теста зелёные, ruff/mypy чисто.

**Бандл 2/3 «add-real-embeddings-reranker» — РЕАЛИЗОВАН (до коммита, mock-тесты + live):**

1. **Embeddings Service (:8004, bge-m3)** — `prototype/src/graphrag_proto/embeddings_service/`:
   `EmbeddingProvider` (нормализованные векторы, dims по умолчанию 1024), реализации
   `MockEmbeddingProvider` и `SentenceTransformerEmbeddingProvider` (lazy-импорт
   torch/sentence-transformers, устройство cuda с fallback на cpu), сборка из env
   (`EMBEDDINGS_MOCK`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `EMBEDDING_MAX_TOKENS`,
   `EMBEDDINGS_DEVICE`). FastAPI: `/health`, `POST /api/v1/embed`, `POST /api/v1/embed/batch`;
   невалидный JSON → 422, сбой модели → 503. Консольный скрипт `graphrag-embeddings`.
2. **Reranker Service (:8006, bge-reranker-base)** — `reranker_service/`: `ScoreProvider`,
   `MockRerankScorer` (лексический скор, режим `mock`) и `CrossEncoderRerankScorer`
   (lazy, `RERANKER_DEVICE`); `/health`, `POST /api/v1/rerank` (скоры выровнены по порядку
   чанков); 422/503. Скрипт `graphrag-reranker`. Порт 8006 добавлен в `docs/04_services_config.md`.
3. **Адаптеры-клиенты** — `retrieval/adapters/bge.py`: `BgeM3ServiceAdapter` и
   `BgeRerankerAdapter` (URL/таймауты из env, X-API-Key из `AUTH_API_KEY`/`GRAPH_AUTH_API_KEY`,
   fail-fast `RuntimeError`). Зарегистрированы в `ADAPTER_CATALOG`: embeddings —
   `deterministic`, `bge_m3_service`; reranker — `noop`, `bge_reranker`. Появляются в
   `available` Topology :8005 и в списках `build_embedder`/`build_reranker` (factory).
4. **EmbedStage на адаптерах** — `ingestion_service/pipeline/orchestrator.py`: `EmbedStage`
   принимает `embedder` (по умолчанию детерминированный), реальный эмбеддер подключается
   картой `infra_topology.yaml`/env; инвариант L2-04 (consistency ingest/query)
   держится на одной фабрике — тест `test_commit_stage.py` (ReflectedEmbedder).
5. **Образы и compose** — `Dockerfile.embeddings`/`Dockerfile.reranker`: `uv sync` +
   `uv pip install torch sentence-transformers` в `.venv` образа (pyproject/uv.lock не
   трогались, mypy-overrides для torch/ST); сервисы `embeddings-service` и `reranker`
   в `infra/compose.yaml` с env mock/dev и портом 8006.
6. **Тесты и приёмка** — +`test_embeddings_service.py`, `test_reranker_service.py`,
   `test_bge_adapters.py` (стаб HTTP-сервера), расширены `test_factory_adapters.py`,
   `test_topology_service.py`; 169 тестов зелёные, ruff/mypy чисто (59 файлов).
   **Live-приёмка:** mock-контур на реальном стеке — `:8004/health` (dims 1024, mode mock),
   `/api/v1/embed` (1024-dim), `/embed/batch` (2×1024), `:8006` health + rerank
   (упорядоченные скоры 1.0/0.0); **real-режим**: torch+sentence-transformers установлены
   в изолированный venv, проверен реальный инференс на CPU — MiniLM encode (384-dim),
   cross-encoder rerank (графовый чанк top-1); bge-m3/bge-reranker-base подключаются
   через `EMBEDDING_MODEL`/`RERANKER_MODEL` (веса ~2.3 ГБ — по требованию, шаг в Runbook).

**Осталось в M3:** бандл 3/3 `add-semantic-cache` — реализован (см. Этап 11); в M3
остался только низкоприоритетный хвост — native vector index (Neo4j HNSW, ADR-023 OQ2).

**Закрытые хвосты вех (L1-01, L4-01), перед бандлом 2/3:**

1. **L1-01 runtime-активация доменов** — закрыта полностью: `POST /domain/activate`
   (Config :8001) уже переключал активный домен в SQLite; добавлен интеграционный тест
   `tests/test_domain_activation.py` (+3) на pull-механизм (Query `DomainProfileLoader`
   и Glossary резолвят по активному домену БЕЗ рестарта) и раздел Runbook «Домен:
   активация it → library → cinema».
2. **L4-01 поочерёдный запуск профилей** — процедура фазирования GPU закрыта: Runbook
   «GPU-гейтинг» (фаза индексации — ingestion/embeddings, фаза поиска — llm, GPU-профили
   одновременно невозможны), compose-конфиг валиден (`config --quiet`). Физический
   VRAM-прогон с bge-m3 :8004 — в бандле 2/3.

**M3-хвосты: конфигурируемый чанкинг (Chunker) — ЗАВЕРШЕНО (до коммита):**

1. **`Chunker` ABC + детерминированная стратегия** — `ingestion_service/pipeline/chunker.py`:
   интерфейс `Chunker` (`chunk(text) -> list[str]`), `SlidingWindowChunker` (дефолт M1:
   512/64, валидация параметров), `StructureAwareChunker` (заголовки Markdown
   `^#{1,6}\s`, короткая секция — один чанк, .txt — fallback), `LangChainChunker`/
   `LlamaIndexChunker` (optional deps, lazy import, fail-fast `RuntimeError`,
   mypy-overrides в pyproject).
2. **Precedence (вариант 3, per-field fill-if-empty)** — env
   (`INGEST_CHUNKER`/`INGEST_CHUNK_SIZE`/`INGEST_CHUNK_OVERLAP`) > профиль домена
   (`chunking`, Config Service per-job, timeout 2 s, недоступность → fallback на
   namespaces/дефолты) > `namespaces.yaml` (`chunking`) > дефолты M1.
   `build_chunker_for(domain)` — резолвер по задаче; `build_chunker()` — legacy env-only.
3. **`ChunkStage` на DI** — `ChunkStage(chunker=None, resolver=build_chunker_for)` →
   резолвер per-job по `ctx.domain`; жёсткие константы `CHUNK_SIZE`/`CHUNK_OVERLAP`/
   `_sliding_window` удалены из orchestrator.
4. **Документация и тесты** — `docs/02` CHUNK, `docs/04` namespace `chunking`, CONCEPT
   §4.1/§6.5, `data_model.md` (свойства Chunk по факту COMMIT), `docs/chunkers_guide.md`
   (контракт + регламент добавления стратегий, §4 — entry_points-механизм, реализован);
   `test_chunker.py` (20 тестов):
   M1-compat, валидация, structure-aware, все 4 уровня precedence, fallback недоступного
   профиля, fail-fast внешних стратегий. 195 тестов зелёные, ruff/mypy чисто.

**Бандл «add-chunker-entry-points» — ЗАВЕРШЁН (до коммита):**

1. **Generic-реестр** — `graphrag_proto/plugin_registry.py`: discovery группы entry-point'ов
   (`importlib.metadata.entry_points(group=...)`, кэш на процесс, `clear_cache()`),
   `map_plugins`/`load_plugin`/`list_plugins`; ошибки `PluginMissingError`/`PluginLoadError`
   (RuntimeError, без тихого fallback).
2. **Группа `graphrag.chunkers`** — `chunker.py`: `list_chunkers()` (встроенные + плагины,
   без конфликтующих имён), ветка «неизвестное имя → плагин» в `_build_chunker`;
   встроенные стратегии приоритетнее плагинов; результат фабрики — `isinstance(Chunker)`;
   неизвестное имя — `ValueError` со списком доступного.
3. **Тесты** — `tests/test_chunker_plugins.py` (7): резолюция via env и профиль per-job,
   приоритет построенных, fail-fast (сломанный импорт / не тот тип результата),
   неизвестное имя со списком плагинов, `list_chunkers()`.

**M3-хвост: self-contained LLM-образ (по ревью `review.md` ТОП-10 №8).**

1. **Проблема:** сервис `llm` монтировал локальный GGUF bind-mount'ом с дефолтом
   `<local-path>/...` — непереносимый Windows-путь, зависимость рантайма от ФС хоста.
2. **Решение:** `prototype/Dockerfile.llm` — двухстадийная сборка: стадия `weights`
   качает GGUF с Hugging Face (`bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF`,
   pin-ревизия) и проверяет SHA-256; стадия runtime — llama.cpp server с вшитой моделью.
   Compose: `build` из `Dockerfile.llm` (image `ohw/llm:prototype`), bind-mount удалён,
   healthcheck на `curl` (в образе llama.cpp нет `python`). Переносимость/детерминизм —
   источник и хеш проверены по HF API (2026-09-13).

**M3-хвост: X-API-Key на всех HTTP-контурах (по ревью `fast_review2.md` P0: auth Config/Ingestion).**

1. **Проблема:** `X-API-Key` требовали 4 из 6 контуров; Config :8001, Ingestion :8002 и Glossary :8003
   были открыты (анонимный `POST /config/domain/validate`, загрузка документов), хотя `AUTH_API_KEY`
   уже был объявлен в compose.
2. **Решение:** единый `require_key` (401 при отсутствии/неверном ключе; `api_key=""` — выключение
   для тестов) в Config/Ingestion/Glossary + незащищённый `/health`; внутренние клиенты
   config/glossary (чанкер-профиль, DomainProfileLoader, NormalizeStage, glossary active-domain)
   шлют `X-API-Key` из `AUTH_API_KEY`/`GRAPH_AUTH_API_KEY`; healthcheck'и переведены на `/health`.
   Инвариант L5-01 теперь фактически выполнен (security.md §5 — честный чек-лист).

**M3-хвост: SQLite WAL + busy_timeout (по ревью `review_2.md` B-2 / `review_team.md` O-2).**

1. **Проблема:** все сторы конфиг/topology/query/ingestion открывали `sqlite3.connect()` по умолчанию
   (journal=DELETE), конкурентный воркер + HTTP-сервисы могли ловить `database is locked`.
2. **Решение:** общий `connect_sqlite()` в `graphrag_proto/sqlite_utils.py` —
   `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`; применён в config/topology/query
   сторы и обоих сторах ingestion (DocumentRegistry + JobStore); +2 теста прагм.

**M3-хвост: лимит параллельных джоб ingestion + 429 (по ревью `critical_review.md`
worker/PEL/SSE / `fast_review2.md` thread-per-job).**

1. **Проблема:** `Executor` спавнил поток на каждую джобу без лимита — N джоб → N потоков → N*GPU
   (документация декларирует «поочерёдно забирает GPU»); при перегрузке никакого механизма отказа.
2. **Решение:** `Executor(..., max_concurrent=N)` на `threading.BoundedSemaphore(N)`
   (`INGEST_MAX_CONCURRENT`, по умолчанию 2); при заполненных слотах `POST /documents` → `429`
   с пометкой джобы `failed` (а не зависшей `queued`); слот освобождается в `finally`.
   +3 теста (лимит, невалидный N, эндпоинт-429).

**M3-хвост: единый словарь id адаптеров YAML↔фабрика (по ревью `review.md`,
`review_2.md` §4.1, `critical_review.md` №1, `fast_review2.md` P0-1).**

1. **Проблема:** YAML-конфиги (`infra/config/adapters.yaml`, `namespaces.yaml` — namespace
   `adapters` и `storage`) декларировали id `neo4j_graph`, `neo4j_vector`, `ollama`, которых
   нет в каталоге фабрики (`ADAPTER_CATALOG`: `neo4j|inmemory`, `openai|fake`, ...) — SSOT-дрейф,
   «что активно сейчас» не определялось ни одним словарём.
2. **Решение:** значения YAML приведены к каталогу фабрики (`neo4j`, `openai`); добавлен
   контрактный тест `tests/test_adapter_catalog_ssot.py` (каждое значение слота ∈ каталог,
   набор слотов YAML == каталог); доки 04/06/adapters_specification/adapters_guide
   синхронизированы с каталогом.

**M3-хвост: redaction секретов в логах (L5-02; по ревью `critical_review.md` №4,
`fast_review2.md` §7, `review_2.md` §5).**

1. **Проблема:** инвариант L5-02 «секреты не логируются» был заявлен (§5 cекurity.md,
   invariants.md), но не реализован: секреты (X-API-Key/Authorization) могли попадать
   в exc-выводы httpx/requests и json-logs Docker.
2. **Решение:** `graphrag_proto/security.py` — `redact_secrets()` (regex по ключам
   X-API-Key/Authorization/api_key/password/neo4j_password/token/secret + подстановка
   известных значений секретов из env), `RedactingFilter` (msg/args/exc_text),
   `install_redaction()` вызывается в `main()` всех 8 сервисов; security.md §5 — чек.
   +13 тестов.

**M3-хвост: CI (GitHub Actions).**

1. **Проблема:** качество держалось только на локальном прогоне dev-контейнера;
   пулл-реквесты не проверялись автоматически.
2. **Решение:** `.github/workflows/ci.yml` — check via `astral-sh/setup-uv`
   (Python 3.13), `uv sync --group dev` по `uv.lock`, затем `ruff`, `mypy`, `pytest -q`
   (без e2e) из `Dz4/prototype`; триггеры push/PR на master.

**M3-хвост: отказоустойчивость query-контура (по ревью `fast_review2.md` №4–5,
`review_team.md` B-4).**

1. **Проблема:** (a) воркер ловил любую ошибку в loop как `process=True` — при сбое
   транспорта `claim/ack` цикл входил в tight loop без бэкоффа, а задачи после `claim`
   без `ack` терялись при падении воркера (PEL-хвосты); (b) SSE-подписка без keep-alive —
   при простое задачи обрыв клиента не детектился за разумное время;
   (c) `ThreadPoolExecutor(max_workers=2)` создавался на каждый запрос — поток на запрос
   вместо общего пула пайплайна.
2. **Решение:** `TaskQueue.reclaim(worker_id, min_idle_s)` (ABC; InMemory — in-flight-slot
   с таймингами и возврат «простывших» claim без ack; Redis — `XAUTOCLAIM` PEL с
   повторной постановкой в очередь); воркер разделяет транспортные/задачевые ошибки и
   применяет экспоненциальный бэкофф (1→2→4→…→30 с, `WORKER_BACKOFF_BASE_S`/
   `WORKER_BACKOFF_MAX_S`) вместо tight loop + периодический reclaim
   (`TASK_RECLAIM_TIMEOUT_S`/`TASK_RECLAIM_INTERVAL_S`); `events()` шлёт `heartbeat` раз в
   `heartbeat_interval_s` (SSE-эндпоинт рендерит как SSE-комментарий `: heartbeat`, клиент
   уже пропускает такие строки); `QueryPipeline` владеет общим executor'ом
   (`shutdown(wait=False)` — при hot-reload закрывает пул прошлого пайплайна). +7 тестов.

**Планируемый бандл (вне M3–M5, по потребности): «add-source-connectors»** — подключение
внешних источников данных (Jira, TestRail/Test Management, Confluence/Wiki, GitLab) как
коннекторов. План-бандл `openspec/changes/add-source-connectors/` (контракт `SourceProvider`,
регистрация по `doc_type`, гайд `docs/connectors_guide.md`). Коннекторы НЕ реализованы;
точка подключения — до пайплайна (материал → ридер → канонический документ, ADR-021),
ядро и push-контракт `POST /documents` (ADR-018) не меняются.

---

## Сравнение версий (полное)

| Функция                          | v2 (summary) | v3 (concept_v3) | v4 (concept_v4) | v5 (CONCEPT.md) | v6 |
|----------------------------------|--------------|-----------------|-----------------|-----------------|--------|
| Гибридный подход (Graph + Vector)| ✅          | ✅             | ✅             | ✅             | ✅           |
| Мультидоменность                 | ❌          | ❌             | ✅             | ✅             | ✅           |
| Domain Profile (YAML)            | ❌          | ❌             | ✅             | ✅             | ✅           |
| Runtime API для переключения     | ❌          | ❌             | ✅             | ✅             | ✅           |
| ADR                              | ❌          | ✅             | ✅             | ✅             | ✅           |
| Система валидации                | ✅          | ✅             | ✅             | ✅             | ✅           |
| Мультиязычность                  | ❌          | ❌             | ✅             | ✅             | ✅           |
| Adapter Layer                    | ❌          | ❌             | ❌             | ✅             | ✅           |
| Разделение Graph/Vector (ADR-013)| ❌          | ❌             | ❌             | ✅             | ✅           |
| Сторонние адаптеры (entry_points)| ❌          | ❌             | ❌             | ✅             | ✅           |
| Асинхронная Task Queue           | ❌          | ❌             | ❌             | ❌             | ✅           |
| Topology Orchestrator            | ❌          | ❌             | ❌             | ❌             | ✅           |
| C4 / arc42 паспорт               | ❌          | ❌             | ❌             | ❌             | ✅           |

### Архивные артефакты

- **Ревью README (v1–4):** в git-истории (`docs/history/v4/review.md` до удаления).
- **Высокоуровневые диаграммы v4 (PDF/SVG):** удалены из рабочей директории; архивный срез `docs.zip` (диаграммы + снапшот `docs/` v4/v5-эпохи) снят с дерева, восстановим из git: `git show 8a83c72:Dz4/docs.zip > docs.zip` (коммит M0).
- **C4/arc42-паспорт v6.0, снапшоты v4/v6/v7:** консолидированы в этот документ (Этап 4/6/7);
  полные срезы восстановимы из `git log`, замещены `docs/00_hi_level_architecture.md`.

---

## Этап 10: Честный контракт атомарности COMMIT (A-2) + решения по кэшу и ревизии данных

**Дата:** 2026-09-14
**Статус:** Активный код (прототип `prototype/`); бандл реализован, до коммита

### A-2 «architecture-atomic-commit» — ADR-024 (закрывает `review_team.md:40-42`)

1. **Capability-контракт** — `retrieval/adapters/base.py`: `consistency_capability()`
   (`"atomic"|"best_effort"`, дефолт best_effort), `engine_key()` (ключ движка/инстанса),
   `atomic_batch()` на GraphStoreProvider (протокол `AtomicBatch`). InMemory-пара —
   `"atomic"` + `inmemory://local`; Neo4j-пара — `"atomic"` + `engine_key` из URI/БД.
2. **Атомарная пара** (обе оси atomic + общий engine_key) — запись ОБЕИХ осей в одной
   транзакции движка: Neo4j через единый `session.begin_transaction()` (`_Neo4jBatch`),
   InMemory — вложенные `transaction()`-контексты единого процесса. Атомарность ровно
   там, где её даёт движок; никакого 2PC.
3. **Разнородные пары — best_effort + компенсация** (`CommitStage._write_best_effort`):
   commit графа → commit вектора; сбой второй оси → компенсация ОБЕИХ осей по полному
   следу джобы `stale ∪ written` (старые эмбеддинги удаляются вне транзакции —
   закрывает орфан-вектора при re-index, L2-03), джоба `failed` с пометкой
   «компенсировано»; повтор идемпотентен (L2-06).
4. **SSOT** — `capabilities: {graph_store, vector_store}` в `adapters.yaml`/
   `namespaces.yaml`; тесты `test_adapter_catalog_ssot.py`: валидность/покрытие,
   декларация == реализация, пара `atomic` ⇔ равен `engine_key`
   (`test_declared_atomic_pair_is_genuine`). `infra_topology.yaml` — комментарий о
   выводе стратегии из capability пары.
5. **Доки** — ADR-024 в `docs/05_adr_log.md`; L2-04 (`docs/invariants.md`), §2.6.1
   (`docs/adapters_specification.md`), таблица §1.2 (`docs/prototype_requirements.md`);
   дизайн «БД как плагины» с пометкой «требует обсуждения, не реализуется в бандле».
6. **Верификация** — 243 теста (включая re-index-компенсацию
   `test_best_effort_reindex_compensates_stale_vectors`), `ruff check .` и `mypy src`
   чисто; замечания adversarial-review (орфан-вектор в компенсации, мёртвый
   `_RecordingBatch.rollback`, `assert vector is not None`, опечатка ADR) — закрыты.

### Решения для бандла 3/3 «add-semantic-cache» (зафиксированы, реализация позже)

1. TTL+`clear()` — допущение управляемой свежести; «плохие» ответы (пустой text, отказ
   LLM «контекста недостаточно», сбой) не кэшируются; epoch-bump
   `query:sc:<rev>:<domain>` (при `created_new=True`) — planned upgrade path, не
   реализуется; инвалидация по источникам — вне скоупа. Триггеры пересмотра: M4 (Eval),
   M5 (конфигуратор профилей), M6 (коннекторы).
2. **Веха 4-хвост — «Ревизия данных в query-контуре»** (`docs/prototype_requirements.md`,
   обязательна до M5/M6): fingerprint «профиль домена + карта адаптеров + версия данных»
   на основе ADR-014-версий DocumentRegistry + topology-revision; не текущий техдолг,
   а отложенное архитектурное решение (deferred decision).

---

## Этап 11: Semantic Cache на Valkey (бандл 3/3) — закрытие M3

**Дата:** 2026-09-14
**Статус:** Активный код (прототип `prototype/`); бандл реализован, до коммита

### Реализация (M3.3, ADR-025)

1. **`retrieval/semantic_cache.py`** — `SemanticCache` (ABC, concrete `store()` = no-op для
   «плохих» ответов по `should_cache_text`: пустой `text`, отказ LLM «контекста
   недостаточно»), `CachedAnswer(text, sources)`, `InMemorySemanticCache`
   (потокобезопасный bucket по domain, TTL-чистка, `stats()`, `clear()`),
   `RedisSemanticCache` (HASH `query:sc:<domain>`, поле `sc:<sha256(repr(embedding))[:12]>`,
   пайлоад JSON `{embedding,text,sources,ts}`, lazy-`import redis`, client-инъекция для
   тестов, HDEL-чистка при сканировании).
2. **QueryPipeline** — параметр `semantic_cache=None` (выкл = поведение M2); miss →
   полный цикл + запись ответа, `done(cache_hit:false, cache_lookup_s)`; hit → статусы
   `embedding` → `cache{hit:true}` → `done` без обращений к store/LLM, `token` не шлётся,
   `generation_time_s`/`retrieval_time_s` = 0.
3. **`query_service/runtime.py`** — `build_semantic_cache()` из env
   (`SEMANTIC_CACHE_ENABLED`, `SEMANTIC_CACHE_MODE` redis|inmemory,
   `SEMANTIC_CACHE_THRESHOLD` 0.85, `SEMANTIC_CACHE_TTL_S` 3600/0=∞, `QUERY_REDIS_URL`);
   `build_pipeline(adapter_map, semantic_cache=...)`. **`worker.py`** — кэш собирается
   один раз и передаётся и в initial, и в hot-reload lambda → переживает пересборку
   адаптеров (топология).
4. **Доки** — ADR-025, §1 `docs/03_retriever.md` (кэш-шаг 0 + допущение инвалидации),
   `docs/demo_runbook.md` «Семантический кэш (M3.3)», статус M3 в
   `docs/prototype_requirements.md`.
5. **Верификация** — 259 тестов (`test_semantic_cache.py` — кэш, Redis-формат через
   FakeRedis, TTL, «плохие» ответы; hit/miss в `test_retrieval_pipeline.py`;
   hot-reload-preserve в `test_worker_hotreload.py`), `ruff check .` и `mypy src` чисто.
   Коммит `0208c28`.

---

## Этап 12: Ревизия данных в query-контуре — Веха 4-хвост (ADR-026, закрытие ревью №5–№7)

**Дата:** 2026-09-15
**Статус:** Реализовано (прототип `prototype/`, openspec-бандл `data-revision-query-context`)

Закрывает замечания ревью №5 (SC-13: нумерация L2-04/L2-07), №6 (R6-1/R6-2/R6-3),
№7 (S6: поллер ревизий не молчит; вывод 3: оператор видит известные ревизии).

### Реализация (ADR-026)

1. **Ingestion** — `DocumentRegistry.data_revision(domain) -> str | None`: fingerprint
   `sha256(sorted((source_url, content_hash) активных документов))`, идемпотентен (no-op upsert не
   меняет, soft-delete меняет) + `data_revision_updated_at`; endpoint
   `GET /api/v1/ingestion/revision?domain=` (X-API-Key, L5-01; 422 без domain); тесты
   `test_ingestion_revision.py` (8 passed).
2. **Semantic Cache** — `lookup/store(..., revision=None)`: InMemory бакет
   `(domain, revision)` (старые эпохи умирают по TTL); Redis ключ
   `query:sc:<rev>:<domain>` (без rev — `query:sc:<domain>`, мета — `...:<domain>:meta`),
   epoch-bump на смене ревизии; 27 тестов.
3. **QueryPipeline** — `run(..., revision=None)` → `done.revision` (str|None) в любом
   исходе (miss/hit/генерация) — срез «по каким данным собран ответ» для Eval (ADR-015).
4. **Query worker** — `RevisionClient` (поллер «по потребности», пер-доменный кэш, окно
   `REVISION_POLL_INTERVAL_S` дефолт 5 с, 0 = M3-поведение; fail-open: сбой → последняя
   известная ревизия, с берём ничего = None; каждый сбой полла инкрементирует
   `revision_poll_errors_total` — S6); `process_one` запрашивает
   `revisions.revision(task.domain)`; снапшот метрик дополнен `revision_poll_errors_total`
   и `revisions` (известные ревизии доменов) — видимость свежести оператору.
5. **Доки** — `prototype_requirements.md` (чеклист Вехи 4-хвост), `invariants.md` L2-07
   реализован, L4-04 снапшот, ADR-026 → реализовано, `learning/data_revision_analytics.md`
   (нумерация L2-04/L2-07 выровнена).

**Верификация** — `test_ingestion_revision.py`, `test_semantic_cache.py`,
`test_retrieval_pipeline.py` (revision в done, epoch-bump), `test_revision_client.py`,
`test_query_api.py` (передача rev в пайплайн, снапшот метрик); ruff/mypy чисто.

---

## Этап 13: Eval-инфраструктура (Веха 4) — M4, АРТИФАКТЫ eval-датасет + метрики + раннер

**Дата:** 2026-09-16  
**Коммит:** в процессе (после `c9a8dec`)  
**Статус:** реализация

### Что сделано

**M4 Eval Infrastructure** — минимальная eval-система для измерения качества retrieval/generation.

### Компоненты

| Компонент | Файл | Описание |
|-----------|------|----------|
| Retrieval Metrics | `src/graphrag_proto/eval/metrics.py` | Recall@K, Precision@K, MRR@K, nDCG@K |
| Generation Metrics | `src/graphrag_proto/eval/metrics.py` | groundedness, coverage, hallucination_rate (LLM-judge) |
| Lift Report | `src/graphrag_proto/eval/metrics.py` | delta от baseline → target, verdict pass/fail |
| Eval-раннер CLI | `src/graphrag_proto/eval/run_eval.py` | `run_eval.py --domain --mode --questions` |
| Eval-датасеты | `infra/eval/{it,library,cinema}/questions.jsonl` | 50/10/10 вопросов, формат ADR-015 |
| Валидация датасетов | `tests/test_eval_dataset.py` | Парсинг, уникальные id, непустые golden_sources, категории |
| Контрактный тест LLM | `tests/test_llm_openai_adapter.py` | Streaming, non-streaming, ошибки HTTP 500/timeout/refused |

### Документы

- `docs/prototype_requirements.md` §Веха 4 — чеклист обновлён (все пункты отмечены `[x]`)
- `docs/invariants.md` v9 — L2-07 realized (ADR-026)
- `docs/05_adr_log.md` — ADR-015 зафиксирован
- `docs/operations_requirements.md` §5 — A/B-тестирование через eval-метрики

### Замечания / следующие шаги

- `OpenAICompatibleAdapter` уже реализован (контрактный тест создан, интеграционный тест нужен)
- Eval-датасеты questions.jsonl.sample (2 строки) будут заменены
- Lift-отчёт для реального компарирования baseline vs hybrid требует compose-стека

**Верификация** — `tests/test_eval_metrics.py` (10 tests), `tests/test_eval_dataset.py` (12 tests),
`tests/test_llm_openai_adapter.py` (5 tests); ruff чисто.

---

## Этап 14: Политика конкурентной записи COMMIT (бандл `concurrent-ingest-write-policy`) — deadlock M5 и закрытие S2

**Дата:** 2026-09-19  
**Коммит:** `ca9aea9` (код+тесты), доки — в этом обновлении  
**Статус:** S2 закрыт (контракт, retry-loop, сортировка, тесты, ADR-028, L3-06); live-приёмка M5
с `INGEST_MAX_CONCURRENT=2` и S3 (очередь ingestion) — вне этого этапа

### Находка (полный eval M5)

`Neo.TransientError.Transaction.DeadlockDetected` при конкурентной записи COMMIT
(`INGEST_MAX_CONCURRENT=2`). Концепция не декларировала политику конкурентной записи —
зафиксирован пробел, сборка бандла `concurrent-ingest-write-policy` (спека §1–§3, стадии S1→S3).

### Что сделано

**S1 (мгновенный обход M5):** `INGEST_MAX_CONCURRENT=1` в `compose.eval.yaml` — ограничение
среды (паттерн ADR-027), риск №7 в `docs/06` §5.

**S2 (M5-хвост) — реализация:**
- Контракт transient-классификации на провайдерах: `transient_aware()`/`is_transient(exc)`
  (`retrieval/adapters/base.py`, L1-02); Neo4j — `TransientError` + `ServiceUnavailable`,
  развёртка цепочки `__cause__` (`neo4j.py:_is_transient_exc`).
- Retry-loop `_with_commit_retry`: `N_RETRY_COMMIT` повторов после первой попытки (всего N+1),
  backoff `0.2*2^i + jitter(0.1)`; env-параметры валидируются fail-fast при старте;
  non-transient — fail без повторов; компенсация best_effort — только после исчерпания retry
  (UC12-02); причина ошибки сохраняется (`raise ... from exc`).
- Детерминированный порядок записи: `CommitStage._write` + `_upsert_nodes`/`_upsert_edges`
  сортируют по контрактным ключам (UC12-07; снижают вероятность deadlock, не исключают).
- Согласованность soft-delete (UC12-06): `registry.rollback_soft_delete` при окончательном
  отказе хранилищ; повторный `soft_delete_source` безопасен (L2-06).
- Доки: ADR-028 (`docs/05_adr_log.md`), инвариант L3-06 (`docs/invariants.md` v10), риск №7
  (`docs/06` §5), этот этап.

### Верификация

`tests/test_{commit_stage,retry_compensation,transient_classification}.py` —
retry (N повторов → успех), non-transient без повторов, компенсация после исчерпания,
`__cause__`, сортировка, env fail-fast (`N_RETRY_COMMIT=0/1/3`, отрицательные/нечисловые),
`rollback_soft_delete`. Итог: **pytest 378 passed** (2 deselected e2e), **ruff** (src+tests)
чисто, **mypy** чисто (dev-образ `ohw-python:3.13`).

### Замечания / следующие шаги

- Live-приёмка M5 на `INGEST_MAX_CONCURRENT=2` — **выполнена 2026-09-19** (прогон 50 вопросов,
  verdict pass, deadlock в логах отсутствует; `prototype/reports/s2-live/m5/lift_report.json`;
  ретракт дефолта к 2 — 2026-09-22, тик 2.6.2 бандла). Проверка no-op повторной загрузки (L2-06)
  перенесена в бандл `eval-graph-contribution-experiment` (задача 1.3) — EXTRACT там меняется
  на типизированный, ревизия корпуса сменится после переингеста.
- Интеграционный тест Neo4j-адаптера (2.5.7) — выполнен live-прогоном `dz4_live_transient.py`
  (2026-09-20, ALL CHECKS PASSED; временный скрипт вне репо).
- S3: очередь ingestion (`docs/06` §4, стадия «Рост») — N воркеров пишут по правилам ADR-028;
  single-writer — при необходимости. Инварианты не меняются.

---

## Этап 15: Эксперимент «вклад графа» (историческая typed-ontology линия)

> **Superseded 2026-09-25:** обязательные typed labels, `_validate_ontology`, `ensure_schema`
> и parallel graph/vector axes не являются актуальной архитектурой. Целевая corrective-линия —
> `add-lightweight-context-graph` / ADR-031: primitive ingest, optional tags/AI enrichment и
> vector-first bounded graph expansion.

### Находка (обоснование бандла)

ADR-015 не умел атрибутировать прирост графу: абсолютный recall не говорит, что помог граф;
вопросы без `graph_required` размывают агрегат; раннер не сохранял пары «вопрос → ответ»
(прогон без артефактов неотличим от «кажется, стало лучше»); полный прогон с судьёй
блокировал быстрый цикл. Зафиксирован дефект парности: в режиме `both` граф выключался
**в обеих** ветках — сравнение двух baseline'ов (исправлено в стадии 4, `_run_mode`).

### Что сделано

**Стадия 1 — типизированный граф (выборка для среза):** граф-ритривер с typed-запросами
(Neo4j DDL-constraint `uniq_<Type>_<key>`, `node_labels` по типу, 05_schema_v2 extraction).
**Стадия 2 — атрибуция осей:** `done.sources` дополнен аддитивным `axis ∈ {graph, vector}`
(ADR-016-совместимо), включая cache-hit-ветку.
**Стадия 3 — метрики вклада:** `graph_contribution()` — `necessity`, `delta_recall`,
`recall_graph/vector`, `evidence_recall_graph`, считается только на срезе
`golden_graph_evidence=true` (вне среза — `mode="not_measured"`, без интерпретации).
**Стадия 4 — датасет v2 и графовый поднабор:** поля `reasoning_type`, `answerability`,
`as_of`, `evidence_sections`, `evidence_policy` (`graph_required` влечёт
`golden_graph_evidence`), `rubric`; валидация `validate_question` + `--extra-dataset`
(merge с дедупликацией id); `it/questions.jsonl` переразмечен в v2 (50 вопросов);
`it/questions_graph.jsonl` — 16 вопросов (9 `graph_required`), факты сверены с docs.
**Стадия 5 — послойные артефакты (ADR-029, L5-05):** `<out>/run_manifest.json` (условия:
run_id, started_at, code_commit, revision/fingerprint, datasets, k, mode, graph_enabled,
components, generation, flags — пишется один раз), `<out>/qa_log.jsonl` (пары с axis,
append на вопрос, переживает падение), `<out>/lift_report.{json,md}` (+ блок «Условия
прогона», `verdict=n/a` без судьи), `<out>/trace.jsonl` (`--trace`, события pipeline).
Режимы: `--no-judge` (generation.mode=`n/a`), `--retrieval-only` (`skipped`, LLM-контур
не требуется в preflight, `pipeline.run(generate=False)`). Правило парности:
`compare_run_manifests` (факторы `mode`/`graph_enabled`), `--compare-with` → расхождение
> 1 поля → «прогоны не парные», `verdict=invalid`.

### Верификация

`tests/test_eval_dataset.py` (v2-валидация, дедупликация merge), `test_typed_graph.py`,
`test_retrieval_core.py` (axis в done), `test_eval_metrics.py` (graph_contribution,
verdict n/a), `test_run_eval_artifacts.py` (манифест, qa_log, trace, режимы, парность).
Итог: **pytest 420 passed** (2 deselected e2e), **ruff** чисто, **mypy** чисто
(dev-образ `ohw/dz4-dev:0.1.0`). Доки: ADR-029 (Draft), инвариант L5-05 (v11),
`docs/test_plan.md` §5–§6, `prototype/infra/eval/README-minimal.md`.

### Замечания / следующие шаги

- Стадия 7 — парный прогон `--mode both` на живом Neo4j-стеке (одна revision,
  `it/questions.jsonl` + `it/questions_graph.jsonl`); ручной сан-чек выборки пар (n ≥ 10);
  хронометраж fast-loop vs полный; диагностический `--trace` на 1–2 вопросах.
- Проверка no-op переингеста (L2-06, замечание Этапа 14, задача 1.3) — в стадии 4
  EXTRACT переведён на типизированный, ревизия корпуса сменится после переингеста.
- Projection lifecycle (transactional outbox, dual-generation, автоматическая
  переиндексация после смены профиля/онтологии) отложена на M6-Growth /
  pre-connectors; текущий эксперимент использует синхронный COMMIT и одну revision.

---

## Связи с другими документами

| Документ                        | Связано с                        | Тип связи           |
|---------------------------------|-----------------------------------|---------------------|
| CONCEPT.md                      | Все документы в docs/            | Главная спецификация |
| docs/01_ontology_and_domain_profile.md | CONCEPT.md §3          | Подробное описание  |
| docs/02_pipeline_and_normalizer.md    | CONCEPT.md §4           | Техническая детализация |
| docs/03_retriever.md                  | CONCEPT.md §5           | Техническая детализация |
| docs/04_services_config.md            | CONCEPT.md §6           | Техническая детализация |
| docs/05_adr_log.md                    | CONCEPT.md §7           | Подробное обоснование (ADR-001 - ADR-029) |
| docs/06_operations_and_risks.md       | CONCEPT.md §8           | Техническая детализация |
| docs/adapters_specification.md        | CONCEPT.md §2, ADR-012/013 | Контракты интерфейсов |
| docs/adapters_guide.md                | CONCEPT.md §2.5, docs/adapters_specification.md | Инструкция подключения внешних систем |
| docs/api_reference.md                 | docs/03, docs/04, docs/02 | API-контракты сервисов (Query v6 async, Config, Ingestion, Glossary) |
| docs/data_model.md                    | CONCEPT.md §4, docs/01, docs/02, docs/glossary.md | Схема узлов/рёбер и рекомендации expert_reviews |
| docs/infrastructure_stack.md          | docs/04 §1, docs/06 §3–§4, history.md Этап 6 | Справочник контейнеров, СУБД, профилей и лицензий |
| docs/expert_reviews.md                | Все выше                | Внешняя экспертиза    |
| docs/00_hi_level_architecture.md           | Концепция v5, docs/05_adr_log.md (ADR-013) | Актуальная C4-картина; исторический C4-паспорт — в git-истории |
| docs/prototype_requirements.md       | Этап 8, ADR-014/015/016/017/018, infrastructure_stack, README §1/§3/§5 | Требования к прототипу: цель, вехи, гейт; заглушки User Guide/Runbook |
| README.md (v1–4), архивное ревью     | Доступно в git-истории (`docs/history/v4/review.md`) | Архивное ревью |