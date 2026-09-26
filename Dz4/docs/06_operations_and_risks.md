# Документация: Operations, Масштабирования и Матрица Рисков

> **Версия:** v5.0  
> **Последнее обновление:** 2026-09-04

## 1. Полная схема ключей Runtime Config (Config Service namespaces)

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
- Объявлен, но **не читается кодом** (проверено 2026-09-26). Косинусная политика дедупликации
  не реализована, см. инвариант `L3-02a` и план `docs/plans/cosine-dedup.md`.
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

**namespace: storage**
- `graph_store` ("neo4j") — optional backend для graph experiment: Neo4jGraphStore / MemgraphGraphStore; baseline может работать без него.
- `vector_store` ("neo4j") — векторная ось: Neo4jVectorStore / QdrantVectorStore.
- `neo4j_uri` ("bolt://neo4j:7687")

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

---

## 2. Регламент наблюдаемости (Observability) и метрики PromQL

Стек: structlog (Сбор JSON-логов) -> stdout -> Promtail -> Loki -> Grafana  
      Prometheus -> Сбор метрик по порту /metrics -> Grafana  
      DCGM Exporter -> Мониторинг VRAM и температуры GPU

Статус реализации (2026-09-15):
- **Этап A (реализован)** — JSON-снапшот из `worker.loop()`:
  `trigger_metrics snapshot {"domain":"*", "queue_depth":…, "topology_poll_errors_total":…, "cache_*":…}`
  (env `METRICS_SNAPSHOT_INTERVAL_S`, дефолт 30 c; `0` = выключено) — всё в structlog/Loki,
  без новых зависимостей. Сбой топологии больше не глотается: логирует счётчик
  `topology poll failed (N consecutive)`.
- **Этап B (задел, фаза 2)** — Prometheus-экспортеры `/metrics` на query+ingestion
  (`prometheus_client`), профиль `monitoring` запускается после их реализации.

Ключевые бизнес-метрики в Prometheus (все содержат обязательный лейбл {domain}):

- `graphrag_query_total{status, domain}` — Счетчик поисковых запросов
- `graphrag_query_duration_seconds{stage, domain}` — Тайминги (graph/vector/rerank/llm)
- `graphrag_ingestion_duration_seconds{stage, domain}` — Скорость работы пайплайна загрузки
- `graphrag_graph_nodes_total{domain, type}` — Общее число узлов в разрезе типов
- `graphrag_graph_edges_total{domain, type}` — Общее число рёбер в разрезе типов
- `graphrag_dedup_merges_total{stage="exact_key"}` — счётчик слияний по нормализованному ключу
  (замена прежнего `stage="auto|llm"`, который не мог сработать: косинусная политика не реализована)
- `graphrag_validation_errors_total{type, rule, domain}` — Ошибки (structural/semantic)
- `graphrag_canonicalization_total{layer, domain}` — Статистика слоёв нормализатора
- `graphrag_canonicalization_fallback_total{domain}` — Счетчик вызовов ЛЛМ-fallback
- `graphrag_domain_switch_total` — Метрика частоты смены доменов
- `graphrag_adapter_switch_total{adapter_type}` — Метрика смены адаптеров (новое в v5)
- `graphrag_projection_job_total{domain,status}` — запуски и результаты offline projection jobs
- `graphrag_projection_state_total{domain,status}` — состояния `ready/degraded/stale/failed`
- `graphrag_projection_sources_total{domain,result}` — обработанные, skipped и failed sources
- `graphrag_projection_fallback_total{domain,reason}` — vector-only fallback из-за readiness/stale/error

---

## 3. Профили Docker Compose (Управление ресурсами железа)

Запуск компонентов изолирован через Docker Profiles для экономии ОЗУ и VRAM:

- **"config"** — Контейнеры Config Service + Glossary Service. Работают ВСЕГДА.
- **"graph"** — Контейнер Neo4j Community. Работает ВСЕГДА.
- **"topology"** — Topology Orchestrator Service + Topology UI (ADR-019). Выделенный сервис
  и настроечное приложение оператора; включается по требованию.
- **"embeddings"** — Embeddings Service (bge-m3). Включается только на фазе индексации.
- **"ingestion"** — Ingestion API + Скрипты пайплайна. Поочерёдно забирает GPU.
- **"llm"** — Query API + llama.cpp (Qwen 2.5 Coder 7B Abliterate q4_K_M). Включается на фазе поиска.
- **"reranker"** — bge-reranker-base (CPU). Опциональный контейнер фазы поиска.
- **"monitoring"** — Prometheus + Grafana + Loki. **Задел (фаза 2): блок отключён в compose.yaml** —
  нет конфигурации Prometheus и /metrics-экспортов, профиль не запускается до их реализации.

Примечание: Docker-профили не связаны с Domain Profile. Domain Profile — это конфигурация (YAML), а не отдельный контейнер. Переключение домена не требует перезапуска контейнеров.

---

## 4. Стратегия масштабирования (Рост системы в 1000 раз)

| Параметр             | Прототип          | Рост              | Продакшен           |
|----------------------|-------------------|-------------------|---------------------|
| Оркестрация          | Docker Compose    | K3s (Apache 2.0)  | Managed K8s         |
| Векторное хранилище  | Neo4j (native)    | Qdrant (отдельно) | Qdrant (кластер)    |
| Конфиг-хранилище     | SQLite            | Postgres          | etcd (Apache 2.0)   |
| Глоссарий            | YAML + SQLite     | Postgres          | Postgres            |
| Кэш                  | Valkey (semantic cache, ADR-025, M3.3) | Valkey (Redis) | Valkey (кластер)    |
| Query-воркеры        | 1 (ThreadPoolExecutor(2) в пайплайне) | 2–4 (S2 закрыт 2026-09-15: fencing reclaim — worker_id в ack/fail, skip уже-терминальной задачи, идемпотентный commit mark_succeeded; таймауты зависимостей — socket_timeout=2s у клиента очереди, `LLM_TIMEOUT_S` у LLM-адаптера; fail-open кэша — P0) | N + автоскейлинг по `queue_depth` |
| Очередь ingestion    | Синхронно         | Redis / RabbitMQ  | Kafka               |
| Модель LLM           | Qwen 2.5 Coder 7B Abliterate q4_K_M (1 GPU)   | Qwen 14B (1 GPU)  | Qwen 72B (multi-GPU)|
| Модель эмбеддингов   | bge-m3 (1 GPU)    | bge-m3 (1 GPU)    | bge-m3 (отдельный)  |
| GPU                  | 1 (поэтапно)      | 2 (параллельно)\* | N (автоскейлинг)    |

> \* Стадия «Рост: GPU 2 (параллельно)» и T2/T5 реализуемы без изменения инвариантов:
> переквалификация L4-01 принята ADR-027 (инвариант = контракт `LLMInference`; совместное
> размещение моделей — ограничение среды прототипа, а не платформенное требование).
>
> **Триггеры перехода между стадиями** (пороги — эвристики, калибруются по замерам;
> механизм важнее чисел: порог → действие → владелец):
>
> | Триггер | Порог (пример) | Действие |
> |---|---|---|
> | T1 — очередь растёт | `queue_depth > 10` устойчиво ИЛИ p95 `queue_wait > 30 с` | 2-й query-воркер (после предусловий строки «Query-воркеры») |
> | T2 — LLM доминирует | p95 `llm_inference_seconds` > 70% времени запроса | вынос LLM на отдельный хост/GPU (конфигурация адаптера, ADR-022; ADR-027 снимает конфликт с гейтингом) |
> | T3 — кэш деградировал | p95 `cache_lookup_seconds` > 10% времени запроса ИЛИ > 500 записей | per-key записи вместо HGETALL-скана HASH (ревью №7 S3) |
> | T4 — RAM кэша | память HASH-ов > X% лимита Valkey | EXPIRE + cap/LRU (S4) |
> | T5 — ingestion ждёт GPU | джобы ждут гейтинга > Y минут | 2-я GPU / вынос эмбеддера (требует ADR-027) |
>
> **Предусловие триггеров:** экспорт метрик (`query_duration` по стадиям,
> `queue_depth`, `llm_inference_seconds`, `cache_hit_rate`/`cache_lookup_seconds`,
> `topology_poll_errors_total`). Реализована «Этап A»-часть: JSON-снапшот
> `trigger_metrics snapshot {...}` из `worker.loop()` (env
> `METRICS_SNAPSHOT_INTERVAL_S`, дефолт 30 с; поллер-счётчик ошибок включён).
> Prometheus `/metrics` (Этап B) — фаза 2 / задел.

### 4.1. Lifecycle graph/vector-проекций

Graph и vector остаются независимыми адаптерами. Offline projection job хранит
`ProjectionState` в domain scope, использует lease/claim, projection revision и
config fingerprint; он backfill-ит только optional vector metadata. Ошибка job переводит
state в `degraded`/`failed` и оставляет vector baseline доступным. Query включает graph
experiment только для `ready` state с актуальной data revision; `stale`, `pending`,
`degraded`, `failed` и missing state дают degraded vector-only fallback.

Полный transactional outbox, dual-generation и автоматическая миграция старой typed-базы
остаются на **M6-Growth / pre-connectors**. До этой стадии offline rebuild не обещает
неограниченную координацию writers, а профиль загруженного корпуса считается неизменным.

Redis-клиенты очереди и semantic cache используют значения секции `redis` из
`prototype/infra_topology.yaml`: явный ограниченный pool (`max_connections=32`),
`socket_connect_timeout=2s`, `socket_timeout=2s` и `retry_on_timeout=false`. Эти
же поля доступны в Topology Configurator через `GET/PUT /api/v1/config/redis`;
после изменения через API Query API/Worker перезапускаются, а hot reload применяется
к карте адаптеров. Долгий LLM-ответ ограничивается общим deadline из `LLM_TIMEOUT_S`;
`QueryWorker` не возвращает задачу в reclaim раньше `LLM_TIMEOUT_S + 30s`, поэтому
длинный поток не создаёт ложный «deadlock» и дубли. Жёсткого обрыва ровно на
120 секундах в runtime нет: 120 с — контракт SSE e2e, а не неявный таймаут Redis
или пула.

Offline projection lease настраивается отдельно в `projection.lease_seconds`
секции `infra_topology.yaml` или через `GET/PUT /api/v1/config/projection`; default
`300s`, минимум `30s`, CLI-флаг отсутствует. Job продлевает lease после каждого
source unit, а потеря fence оставляет state без ложного `ready`.

Live Neo4j parity (LP-13) прогоняется маркером `live`
(`tests/test_live_projection_neo4j_parity.py`) и по умолчанию пропускается.
Проверка уже находила дефекты, невидимые на InMemory: вычитание списков
(`list - list`) и `NOT value IN $list` в Cypher Neo4j 5 невалидны и падают с
`Cannot subtract List from List`. В адаптере используется list comprehension с
`any(...)`; изменение затрагивает и ingest-путь, поэтому live-маркер обязателен
перед приёмкой изменений `_upsert_nodes`/`_upsert_edges`/`update_vector_metadata`.

---

## 5. Матрица критических рисков

Примечание: Ниже зафиксированы 6 критических инфраструктурных рисков текущей фазы.

| № | Риск                                               | Митигация                                            |
|---|----------------------------------------------------|------------------------------------------------------|
| 1 | GPU-конфликт памяти между bge-m3 и Qwen 2.5 Coder 7B Abliterate на одной карте RTX 2070 Super *(ограничение среды прототипа, не платформенный инвариант — ADR-027)* | Жёсткое поочерёдное использование видеокарты через Docker Compose Profiles |
| 2 | OOM (Out of Memory) СУБД Neo4j на хост-машине с 16 ГБ ОЗУ | Ограничение JVM (max heap 1G, pagecache 512M) через параметры контейнера |
| 3 | Ложные склейки сущностей нормализатором | Слияние только при точном совпадении нормализованного canonical key; неоднозначные варианты не объединяются молча. Косинусная двухступенчатая проверка — план, не реализована (`L3-02a`) |
| 4 | Раздувание и замусоривание графа связями SIMILAR_TO | Порог `similar_to_threshold` не реализован; вид `SIMILAR_TO` приходит из промпта экстракции. Объём ограничивают бюджеты обхода, а не порог |
| 5 | Логические противоречия и галлюцинации ЛЛМ на этапе Extraction | Работа LLM (по умолчанию Qwen 2.5 Coder 7B Abliterate) на низкой температуре (0.1) + тотальная семантическая проверка графа Cypher-запросами в Validator v2 до сохранения изменений (не реализована, ADR-031) |
| 6 | Vendor lock-in — привязка к конкретному поставщику инфраструктуры (новое в v5) | Слой адаптеров (ADR-012) позволяет заменить любой компонент инфраструктуры через runtime config без переписывания ядра |

> **Риск №8 (исторический, superseded 2026-09-25):** typed-ontology mismatch и отсутствие
> runtime DDL больше не являются целевым риском. Corrective change
> `add-lightweight-context-graph` переводит ingest на primitive optional enrichment и
> убирает обязательные labels/constraints. Старые записи и typed-данные в Neo4j не удаляются
> автоматически; их cleanup — отдельная migration.
>
> **Риск graph experiment:** offline projection может отставать от vector baseline или быть
> частично построена. Это не failed ingest: query помечает такой запуск degraded и возвращает
> vector-only result; readiness/projection revision фиксируются в manifest и trace.
>
> **Риск №7 (S1-обход, закрыт S2 — ADR-028):** `Neo4j deadlock` при конкурентной записи
> COMMIT (`INGEST_MAX_CONCURRENT>1`, находка полного eval M5 — `TransientError.DeadlockDetected`).
> S1-обход на время M5: `INGEST_MAX_CONCURRENT=1` в `compose.eval.yaml` (ограничение среды,
> паттерн ADR-027 — не инвариант). S2-решение (бандл `concurrent-ingest-write-policy`,
> ADR-028, инвариант L3-06): retry transient до `N_RETRY_COMMIT` + детерминированный порядок
> записи (сортировка `nodes`/`edges` по контрактным ключам) + согласованность soft-delete
> (`rollback_soft_delete`); возврат дефолта `INGEST_MAX_CONCURRENT=2`. Deadlock не исключается
> полностью — retry обязателен. Дальнейший масштаб — очередь ingestion (§4, стадия «Рост»).