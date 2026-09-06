# Документация: Конфигурация Сервисов и Runtime API

> **Версия:** v5.0  
> **Последнее обновление:** 2026-09-04

## 1. Сетевая архитектура и карта портов в выделенной сети Docker (ohw_net)

Все сервисы изолированы внутри единой виртуальной Docker-сети с именем "ohw_net" (Ollama Homework Network). Проекты общаются друг с другом по именам контейнеров.

| Сервис              | Порт          | Назначение                                         |
|---------------------|---------------|----------------------------------------------------|
| Query API           | 8000          | Поисковые запросы и генерация ответов (в т.ч. MCP-интеграция ИИ-агентов) |
| Config Service      | 8001          | Хранение Feature Flags + Управление Domain Profile + Adapters API |
| Ingestion API       | 8002          | Управление фоновыми джобами индексации            |
| Glossary Service    | 8003          | Канонизация и загрузка glossary.{profile}.yaml     |
| Embeddings Service  | 8004          | Расчёт векторов bge-m3 на GPU (опционально, если не используется встроенный адаптер) |
| Topology Orchestrator Service | 8005 | Фабрика провайдеров по `prototype/infra_topology.yaml`, runtime-переключение адаптеров (ADR-019) |
| Web UI — Конфигуратор (Streamlit) | 8501 | Веб-панель «Бизнес-онтология» (Domain Profile, глоссарии) |
| Topology UI (Streamlit) | 8502 | Настроечное приложение «Топология инфраструктуры» (оператор, ADR-019) |
| Neo4j Database      | 7687 (Bolt)   | Bolt-интерфейс для работы с графом                 |
| Neo4j Database      | 7474 (HTTP)   | Web UI браузера для Neo4j                          |
| Ollama Server       | 11434         | Инференс локальной модели Qwen 2.5 7B             |

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

## 3. Adapters Management API (новое в v5)

Управление слоем адаптеров осуществляется через REST API без перезапуска контейнеров:

- `GET /api/v1/config/adapters` — Получить текущие адаптеры
- `PUT /api/v1/config/adapters` — Изменить адаптеры (переключение на лету)
- `GET /api/v1/config/adapters/available` — Получить список доступных адаптеров (включая плагины)

Примеры:

```bash
# Получение текущих адаптеров
curl http://localhost:8001/api/v1/config/adapters

# Смена векторной оси хранилища на Qdrant
curl -X PUT http://localhost:8001/api/v1/config/adapters \
  -d '{"vector_store": "qdrant"}'

# Получение списка доступных адаптеров (включая плагины)
curl http://localhost:8001/api/v1/config/adapters/available
```

---

## 4. Структура изолированных глоссарей

**Роль Glossary Service (трансляция тегов):** лингвист/аналитик работает с привычной GUI-моделью «тегов» — синонимы и варианты записи для канонического термина (экран «Бизнес-онтология» конфигуратора, CONCEPT §6.4). Сервис скрывает технический формат и отдаёт графу канонический ряд (`canonical_name`). Авторинг ведётся через конфигуратор, а не напрямую в БД.

**Валидация уникальности:** по запросу `POST /api/v1/glossary/validate` сервис проверяет, что в рамках домена один тег не ведёт к двум каноническим терминам и наоборот, а также пересечение словаря с уже активированными профилями. Расширенная glossary-валидация через секцию `glossary` эндпоинта `POST /api/v1/config/domain/validate` в M0 не реализуется (config-validate проверяет структуру профиля) — запланирована на M1.

**ИИ-подсказки тегов (опция):** при новой экстракции агент может предлагать кандидатов-синонимов из корпуса документов, лингвист подтверждает или отклоняет. Не является обязательным для работы пайплайна.

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
- `model` ("qwen2.5:7b-instruct")
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
- `model` ("qwen2.5:7b-instruct")
- `temperature` (0.3)
- `max_tokens` (2048)
- `context_window` (32768)

**namespace: embeddings**
- `model` ("bge-m3")
- `dimensions` (1024)
- `max_tokens` (8192)
- `batch_size` (32)

**namespace: storage**
- `graph_store` ("neo4j_graph") — графовая ось: Neo4jGraphStore / MemgraphGraphStore.
- `vector_store` ("neo4j_vector") — векторная ось: Neo4jVectorStore / QdrantVectorStore.
- `neo4j_uri` ("bolt://neo4j:7687")

**namespace: auth**
- `api_key` ("changeme")

**namespace: flags**
- `graph_search_enabled`
- `reranker_enabled`
- `similar_to_expansion`
- `semantic_validation`

**namespace: adapters (новое в v5)**
- `graph_store` ("neo4j_graph")
- `vector_store` ("neo4j_vector")
- `llm` ("ollama")
- `embeddings` ("bge_m3_service")
- `reranker` ("bge_reranker")