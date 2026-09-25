# Документация: Конфигурация Сервисов и Runtime API

> **Версия:** v5.1  
> **Последнее обновление:** 2026-09-11

## 1. Сетевая архитектура и карта портов в выделенной сети Docker (ohw_net)

Все сервисы изолированы внутри единой виртуальной Docker-сети с именем "ohw_net" (Ollama Homework Network). Проекты общаются друг с другом по именам контейнеров.

| Сервис              | Порт          | Назначение                                         |
|---------------------|---------------|----------------------------------------------------|
| Query API           | 8000          | Поисковые запросы и генерация ответов (в т.ч. MCP-интеграция ИИ-агентов) |
| Config Service      | 8001          | Хранение Feature Flags + Управление Domain Profile (Adapters API — на Topology :8005) |
| Ingestion API       | 8002          | Управление фоновыми джобами индексации            |
| Glossary Service    | 8003          | Канонизация и загрузка glossary.{profile}.yaml     |
| Embeddings Service  | 8004          | Расчёт векторов bge-m3 на GPU (опционально, если не используется встроенный адаптер) |
| Topology Orchestrator Service | 8005 | Фабрика провайдеров по `prototype/infra_topology.yaml`, runtime-переключение адаптеров (ADR-019) |
| Reranker Service    | 8006          | Реранкинг чанков bge-reranker-base на CPU (опционально; NoOp-адаптер допустим) |
| Web UI — Конфигуратор (Streamlit) | 8501 | Веб-панель «Бизнес-онтология» (Domain Profile, глоссарии) |
| Topology UI (Streamlit) | 8502 | Настроечное приложение «Топология инфраструктуры» (оператор, ADR-019) |
| Neo4j Database      | 7687 (Bolt)   | Bolt-интерфейс для работы с графом                 |
| Neo4j Database      | 7474 (HTTP)   | Web UI браузера для Neo4j                          |
| llama.cpp Server   | 8080 (/v1)    | Инференс Qwen 2.5 Coder 7B Abliterate q4_K_M (GGUF) |

---

## 2. Runtime Domain Management API (Config Service)

Управление доменными конфигурациями осуществляется через REST API без перезапуска контейнеров:

- `GET /api/v1/config/domain/active` — Получить имя активного профиля
- `GET /api/v1/config/domain/profiles` — Получить список доступных YAML-профилей
- `GET /api/v1/config/domain/profile/{name}` — Получить содержимое профиля {name}
- `POST /api/v1/config/domain/validate` — Валидация структуры YAML-профиля (400 — битый YAML/не-маппинг; невалидная структура — 200 `{valid, errors}`)
- `POST /api/v1/config/domain/profile` — Загрузка нового профиля (фаза конфигуратора, M5+)
- `POST /api/v1/config/domain/activate` — Рантайм-переключение активного домена (конфликт `profile.name` vs `domain` — 422)

---

## 3. Runtime Config Management API (адаптеры, Redis, projection)

Управление слоем адаптеров осуществляется через REST API без перезапуска контейнеров:

- `GET /api/v1/config/adapters` — Получить текущие адаптеры
- `PUT /api/v1/config/adapters` — Изменить адаптеры (переключение на лету)
- `GET /api/v1/config/adapters/available` — Получить список доступных адаптеров (включая плагины)

Хост API — **Topology Orchestrator :8005** (ADR-019): управление топологией вынесено
из Config Service в отдельный сервис с профилем `topology` (при неподнятом профиле
воркер работает по env с fallback). Форматы запросов/ответов сохранены.

### 3.1. Redis/Valkey runtime settings

Источник настроек клиентов Redis — секция `redis` в `prototype/infra_topology.yaml`.
Она одновременно является базой Topology Configurator; сервисы Query API/Worker
читают файл при старте через `INFRA_TOPOLOGY_PATH`.

Поля:

- `url` — URL Valkey/Redis с базой;
- `socket_timeout_s`, `socket_connect_timeout_s` — таймауты чтения и подключения;
- `max_connections` — верхняя граница пула клиента;
- `retry_on_timeout` — не использовать автоматический retry при timeout;
- `health_check_interval_s` — интервал проверки соединения;
- `read_block_ms` — блокирующий read блока очереди/SSE.

Configurator API (`:8005`, требуется `X-API-Key`):

- `GET /api/v1/config/redis` — effective settings и `revision`;
- `PUT /api/v1/config/redis` — изменить любое подмножество полей; значения
  валидируются, неизвестные поля и неверные типы дают `422`.

Пример:

```bash
curl -H "X-API-Key: $GRAPH_AUTH_API_KEY" http://localhost:8005/api/v1/config/redis
curl -X PUT -H "X-API-Key: $GRAPH_AUTH_API_KEY" -H 'Content-Type: application/json' \
  http://localhost:8005/api/v1/config/redis \
  -d '{"max_connections": 16, "socket_connect_timeout_s": 3}'
```

`QUERY_REDIS_URL`/`REDIS_URL` и `REDIS_*` — deployment overrides отдельных полей.
Они имеют приоритет над YAML для процесса, который их запускает. Изменение
через Configurator сохраняется в `topology_data`; уже запущенные Query API/Worker
подхватывают его при перезапуске (hot reload применяется к адаптерам, не к
пулу соединений).

### 3.2. Offline projection lease policy

`OfflineProjectionJob` — операторская job, а не адаптер Redis. В конфигураторе
настраивается только lease-политика, а не класс `ProjectionStateStore`:

- `projection.lease_seconds` — lease одного rebuild; default `300` секунд,
  минимум `30`; job продлевает lease после каждого source unit;
- при потере lease job не публикует терминальный state и может быть безопасно
  повторён другим claimant;
- значение фиксируется на момент запуска job; изменение применяется к следующему
  rebuild, а hot reload относится к карте адаптеров.

Configurator API (`:8005`, `X-API-Key`):

- `GET /api/v1/config/projection` — effective policy и `revision`;
- `PUT /api/v1/config/projection` — изменить `lease_seconds`; неверное значение
  или неизвестное поле дают `422`.

```bash
curl -H "X-API-Key: $GRAPH_AUTH_API_KEY" http://localhost:8005/api/v1/config/projection
curl -X PUT -H "X-API-Key: $GRAPH_AUTH_API_KEY" -H 'Content-Type: application/json' \
  http://localhost:8005/api/v1/config/projection -d '{"lease_seconds": 300}'
```

Источник по умолчанию — секция `projection` в `prototype/infra_topology.yaml`;
override Configurator сохраняется в `topology_data`. `PROJECTION_LEASE_SECONDS`
— аварийный deployment override для процесса, CLI-флаг намеренно не используется.

Примеры:

```bash
# Получение текущих адаптеров
curl -H "X-API-Key: $GRAPH_AUTH_API_KEY" http://localhost:8005/api/v1/config/adapters

# Смена векторной оси хранилища на inmemory (валидные id — из available)
curl -X PUT -H "X-API-Key: $GRAPH_AUTH_API_KEY" http://localhost:8005/api/v1/config/adapters \
  -d '{"vector_store": "inmemory"}'

# Получение списка доступных адаптеров (включая плагины)
curl -H "X-API-Key: $GRAPH_AUTH_API_KEY" http://localhost:8005/api/v1/config/adapters/available
```

---

## 4. Структура изолированных глоссарей

**Роль Glossary Service (alias resolver):** сервис связывает варианты записи, синонимы и переводы с `tag_id`/`canonical_name` графа. Он optional для primitive ingest и не навязывает фиксированную ontology. AI может предлагать aliases/merge, но ручные tags имеют приоритет; неоднозначные кандидаты не объединяются молча.

**Валидация:** `POST /api/v1/glossary/validate` проверяет конфликты alias map, а не fixed node types.

**ИИ-подсказки тегов (опция):** при новой экстракции агент может предлагать кандидатов-синонимов из корпуса документов, лингвист подтверждает или отклоняет. Не является обязательным для работы пайплайна.

Graph projection — отдельный experiment: её можно строить inline после vector commit или
идемпотентным offline replay; `ProjectionState` хранит readiness/revision и не является
prerequisite для baseline.

Glossary Service подгружает файл глоссария по активному домену (pull-модель: при запросе
без `domain` сервис берёт активный профиль из Config Service: `GET /api/v1/config/domain/active`,
fallback `it`). Каталог профилей прототипа: `prototype/domain_profiles/`:

- `glossary.library.yaml` — содержит terms (авторы), genre_aliases, pen_names, unicode_map
- `glossary.cinema.yaml` — содержит terms (фильмы), director_aliases, genre_aliases
- `glossary.it.yaml` — содержит terms, data_types, complexity_aliases, unicode_map, а также секцию function_synonyms

### Содержание function_synonyms в glossary.it.yaml (правила для слоя 2 канонизации):

```yaml
log: ["lg", "ln", "log_2", "log_10", "log2", "logn", "log₂", "log₁₀"]
sqrt: ["√", "cbrt"]
factorial: ["!", "fact"]
```

---

## 5. Конфигурация сервисов

### 5.1. Полный набор ключей runtime config (Config Service namespaces)

**namespace: domain**
- `active_profile` ("it")
- `profiles_available`
- `auto_reload`

**namespace: retrieval**
- `cosine_threshold` (0.7)
- `max_graph_nodes` (5)
- `max_vector_chunks` (5)
- `context_size` (4096)
- `reranker_enabled`
- `graph_search_enabled`
- `similar_to_expansion`

**namespace: extraction**
- `model` ("qwen2.5-coder-7b-instruct-abliterated-q4_k_m")
- `temperature` (0.1)
- `max_tokens` (4096)
- Примечание: доменный prompt_template подгружается динамически из профиля

**namespace: normalizer**
- `dedup_auto_threshold` (0.92)
- `dedup_llm_threshold` (0.75)
- `similar_to_threshold` (0.85)
- `llm_canonicalize_fallback` (true)
- `unicode_normalization` (true)
- `log_normalization` (true)

**namespace: llm**
- `model` ("qwen2.5-coder-7b-instruct-abliterated-q4_k_m")
- `temperature` (0.3)
- `max_tokens` (2048)
- `context_window` (32768)

**namespace: embeddings**
- `model` ("bge-m3")
- `dimensions` (1024)
- `max_tokens` (8192)
- `batch_size` (32)

**Принцип выбора размерности:** default `dimensions` (1024) — фактический
выходной размер модели `bge-m3`. При смене модели размерность обязана равняться
её выхлопу (например MiniLM-L6-v2 → 384, тогда `EMBEDDING_DIMENSIONS=384`),
иначе сервис вернёт `503`, а HTTP-адаптер — fail-fast при несовпадении. Mock и
real-режимы, а также клиент (`BgeM3ServiceAdapter`) используют строго одну и
ту же размерность — ось эмбеддингов (L2-04) не ломается при переключении.

**namespace: chunking (новое в v5.1, M3-хвосты)**
- `strategy` ("sliding_window") — выбрать чанкер: `sliding_window` | `structure_aware` | `langchain` | `llamaindex`
- `chunk_size` (512) — размер окна (в словах/токенах для sliding-window)
- `overlap` (64) — перекрытие окон (строго в [0, chunk_size))
- **Precedence (per-field, вариант 3):** env (`INGEST_CHUNKER`/`INGEST_CHUNK_SIZE`/`INGEST_CHUNK_OVERLAP`) >
  профиль домена (секция `chunking`, тянется per-job через `GET /api/v1/config/domain/profile/{domain}`
  из Config Service, `CONFIG_URL`) > namespaces.yaml (`chunking`) > дефолты M1 (512/64, sliding_window).
  Каждое поле берётся из первого источника, где оно задано; профиль недоступен → fallback на
  namespaces/дефолт (чанкинг — некритичная настройка, ingest не падает).
- env-параметры: `INGEST_CHUNKER`, `INGEST_CHUNK_SIZE`, `INGEST_CHUNK_OVERLAP`
  (дополнительно: `INGEST_LANGCHAIN_SPLITTER`, `INGEST_LLAMAINDEX_PARSER`).
  LangChain/LlamaIndex — optional dependency (group `[chunking]`), lazy import;
  без установленного пакета выбранный адаптер даёт fail-fast при вызове.

**namespace: storage**
- `graph_store` ("neo4j") — optional backend для graph experiment: Neo4jGraphStore / MemgraphGraphStore; baseline может работать без него.
- `vector_store` ("neo4j") — векторная ось: Neo4jVectorStore / QdrantVectorStore.
- `neo4j_uri` ("bolt://neo4j:7687")
- `PROJECTION_STATE_DB_PATH` (`runtime/projection.db`) — SQLite state store offline projection jobs
- `PROJECTION_CONFIG_FINGERPRINT` (`default`) — конфигурационный фактор projection revision

**namespace: projection**
- `lease_seconds` (300, минимум 30) — lease offline projection job; renew выполняется автоматически

**namespace: auth**
- `api_key` ("changeme")

**namespace: flags**
- `graph_search_enabled`
- `reranker_enabled`
- `similar_to_expansion`
- `semantic_validation`

**namespace: adapters (новое в v5)**
- `graph_store` ("neo4j")
- `vector_store` ("neo4j")
- `llm` ("openai")
- `embeddings` ("bge_m3_service")
- `reranker` ("bge_reranker")

Допустимые id каждого слота — каталог фабрики `retrieval/adapters/factory.py::ADAPTER_CATALOG`
(SSOT по контракту `tests/test_adapter_catalog_ssot.py`: значение YAML ∈ каталог слота).