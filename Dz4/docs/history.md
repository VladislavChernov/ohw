# История разработки концепции GraphRAG

> **Версия:** v10 (реализация прототипа: вехи M0–M2, M3-бандлы адаптеров+topology, embeddings+reranker; M3-хвосты чанкинга+плагинов; self-contained LLM-образ; X-API-Key на всех HTTP-контурах; SQLite WAL+busy_timeout; лимит параллельных джоб ingestion c 429; единый словарь id адаптеров YAML↔фабрика; redaction секретов L5-02)
> **Последнее обновление:** 2026-09-13

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

v6 — следующая итерация концепции и документации поверх базы v5: SSOT-документы v5 (CONCEPT.md, `docs/`) остаются базой, актуальная картина архитектуры — `docs/00_hi_level_architecture.md` (v6). v6-специфичные артефакты (C4-паспорт, ADR-013, Task Queue) заархивированы в `docs/history/v6/`.

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

Конкретика, вычищенная из CONCEPT, консервирована как **архивная итерация** в
`docs/history/v7/` (снапшот `CONCEPT.md` v5.0 до чистки). Он служит материалом для сборки
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

**Осталось в M3:** бандл 3/3 «add-semantic-cache» (Valkey-кэш семантических
запросов).

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

- **Ревью README (v1–4):** перенесено в [docs/history/v4/review.md](./history/v4/review.md).
- **Высокоуровневые диаграммы v4 (PDF/SVG):** удалены из рабочей директории, исторические копии сохранены в `docs.zip`.

---

## Связи с другими документами

| Документ                        | Связано с                        | Тип связи           |
|---------------------------------|-----------------------------------|---------------------|
| CONCEPT.md                      | Все документы в docs/            | Главная спецификация |
| docs/01_ontology_and_domain_profile.md | CONCEPT.md §3          | Подробное описание  |
| docs/02_pipeline_and_normalizer.md    | CONCEPT.md §4           | Техническая детализация |
| docs/03_retriever.md                  | CONCEPT.md §5           | Техническая детализация |
| docs/04_services_config.md            | CONCEPT.md §6           | Техническая детализация |
| docs/05_adr_log.md                    | CONCEPT.md §7           | Подробное обоснование (ADR-001 - ADR-020) |
| docs/06_operations_and_risks.md       | CONCEPT.md §8           | Техническая детализация |
| docs/adapters_specification.md        | CONCEPT.md §2, ADR-012/013 | Контракты интерфейсов |
| docs/adapters_guide.md                | CONCEPT.md §2.5, docs/adapters_specification.md | Инструкция подключения внешних систем |
| docs/api_reference.md                 | docs/03, docs/04, docs/02 | API-контракты сервисов (Query v6 async, Config, Ingestion, Glossary) |
| docs/data_model.md                    | CONCEPT.md §4, docs/01, docs/02, docs/glossary.md | Схема узлов/рёбер и рекомендации expert_reviews |
| docs/infrastructure_stack.md          | docs/04 §1, docs/06 §3–§4, history.md Этап 6 | Справочник контейнеров, СУБД, профилей и лицензий |
| docs/expert_reviews.md                | Все выше                | Внешняя экспертиза    |
| docs/history/v6/ (C4-паспорт v6.0)  | Концепция v5, docs/05_adr_log.md (ADR-013) | Архив C4; замещено 00_hi_level_architecture.md |
| docs/history/v7/ (агностификация)    | CONCEPT.md v5.0, Этап 7   | Архив вычищенной конкретики; материал прототипа |
| docs/prototype_requirements.md       | Этап 8, ADR-014/015/016/017/018, infrastructure_stack, README §1/§3/§5 | Требования к прототипу: цель, вехи, гейт; заглушки User Guide/Runbook |
| docs/history/v4/review.md             | README.md (v1–4)         | Архивное ревью      |