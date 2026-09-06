# Документация: Конфигурация Сервисов и Runtime API (v4.0 — АРХИВ)

> **Версия:** v4.0  
> **Последнее обновление:** 2026-09-04

## 1. Сетевая архитектура и карта портов в выделенной сети Docker (ohw_net)

Все сервисы изолированы внутри единой виртуальной Docker-сети с именем "ohw_net" (Ollama Homework Network). Проекты общаются друг с другом по именам контейнеров.

| Сервис              | Порт          | Назначение                                         |
|---------------------|---------------|----------------------------------------------------|
| Query API           | 8000          | Поисковые запросы и генерация ответов              |
| Config Service      | 8001          | Хранение Feature Flags + Управление Domain Profile API |
| Ingestion API       | 8002          | Управление фоновыми джобами индексации            |
| Glossary Service    | 8003          | Канонизация и загрузка glossary.{profile}.yaml     |
| Embeddings Service  | 8004          | Расчёт векторов bge-m3 на GPU                      |
| Neo4j Database      | 7687 (Bolt)   | Bolt-интерфейс для работы с графом                 |
| Neo4j Database      | 7474 (HTTP)   | Web UI браузера для Neo4j                          |
| Ollama Server       | 11434         | Инференс локальной модели Qwen 2.5 7B             |

## 2. Runtime Domain Management API (Config Service)

Управление доменными конфигурациями осуществляется через REST API без перезапуска контейнеров:

- `GET /api/v1/config/domain/active` — Получить имя активного профиля
- `GET /api/v1/config/domain/profiles` — Получить список доступных YAML-профилей
- `GET /api/v1/config/domain/profile/{name}` — [ВОЗВРАЩЕНО] Получить содержимое профиля {name}
- `POST /api/v1/config/domain/validate` — Валидация структуры нового YAML-профиля
- `POST /api/v1/config/domain/profile` — Загрузка нового профиля в систему
- `POST /api/v1/config/domain/activate` — Рантайм-переключение активного домена

## 3. Структура изолированных глоссарей

Glossary Service подгружает соответствующий файл глоссария вслед за активацией домена:

- `glossary.library.yaml` — содержит terms (авторы), genre_aliases, pen_names, unicode_map
- `glossary.cinema.yaml` — содержит terms (фильмы), director_aliases, genre_aliases
- `glossary.it.yaml` — содержит terms, data_types, complexity_aliases, unicode_map, а также секцию function_synonyms [ВОЗВРАЩЕНО]

### Содержание function_synonyms в glossary.it.yaml (правила для слоя 2 канонизации):

```yaml
log: ["lg", "ln", "log_2", "log_10", "log2", "logn", "log₂", "log₁₀"]
sqrt: ["√", "cbrt"]
factorial: ["!", "fact"]
```

## 4. Конфигурация сервисов

### 4.1. Полный набор ключей runtime config (Config Service namespaces)

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

**namespace: extraction [ВОЗВРАЩЕНО]**
- `model` ("qwen2.5:7b-instruct")
- `temperature` (0.1)
- `max_tokens` (4096)
- Примечание: доменный prompt_template подгружается динамически из профиля

**namespace: normalizer [ВОЗВРАЩЕНО]**
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

**namespace: storage [ВОЗВРАЩЕНО]**
- `neo4j_uri` ("bolt://neo4j:7687")
- `vector_index` ("chunk_embeddings")

**namespace: auth [ВОЗВРАЩЕНО]**
- `api_key` ("changeme")

**namespace: flags**
- `graph_search_enabled`
- `reranker_enabled`
- `similar_to_expansion`
- `semantic_validation`