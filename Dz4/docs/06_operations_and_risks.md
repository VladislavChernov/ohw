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

---

## 2. Регламент наблюдаемости (Observability) и метрики PromQL

Стек: structlog (Сбор JSON-логов) -> stdout -> Promtail -> Loki -> Grafana  
      Prometheus -> Сбор метрик по порту /metrics -> Grafana  
      DCGM Exporter -> Мониторинг VRAM и температуры GPU

Ключевые бизнес-метрики в Prometheus (все содержат обязательный лейбл {domain}):

- `graphrag_query_total{status, domain}` — Счетчик поисковых запросов
- `graphrag_query_duration_seconds{stage, domain}` — Тайминги (graph/vector/rerank/llm)
- `graphrag_ingestion_duration_seconds{stage, domain}` — Скорость работы пайплайна загрузки
- `graphrag_graph_nodes_total{domain, type}` — Общее число узлов в разрезе типов
- `graphrag_graph_edges_total{domain, type}` — Общее число рёбер в разрезе типов
- `graphrag_dedup_merges_total{stage="auto|llm"}` — Счетчик авто- и ЛЛМ-мержей
- `graphrag_validation_errors_total{type, rule, domain}` — Ошибки (structural/semantic)
- `graphrag_canonicalization_total{layer, domain}` — Статистика слоёв нормализатора
- `graphrag_canonicalization_fallback_total{domain}` — Счетчик вызовов ЛЛМ-fallback
- `graphrag_domain_switch_total` — Метрика частоты смены доменов
- `graphrag_adapter_switch_total{adapter_type}` — Метрика смены адаптеров (новое в v5)

---

## 3. Профили Docker Compose (Управление ресурсами железа)

Запуск компонентов изолирован через Docker Profiles для экономии ОЗУ и VRAM:

- **"config"** — Контейнеры Config Service + Glossary Service. Работают ВСЕГДА.
- **"graph"** — Контейнер Neo4j Community. Работает ВСЕГДА.
- **"topology"** — Topology Orchestrator Service + Topology UI (ADR-019). Выделенный сервис
  и настроечное приложение оператора; включается по требованию.
- **"embeddings"** — Embeddings Service (bge-m3). Включается только на фазе индексации.
- **"ingestion"** — Ingestion API + Скрипты пайплайна. Поочерёдно забирает GPU.
- **"llm"** — Query API + Ollama (Qwen 7B). Включается на фазе поиска.
- **"reranker"** — bge-reranker-base (CPU). Опциональный контейнер фазы поиска.
- **"monitoring"** — Prometheus + Grafana + Loki. Разворачивается по требованию.

Примечание: Docker-профили не связаны с Domain Profile. Domain Profile — это конфигурация (YAML), а не отдельный контейнер. Переключение домена не требует перезапуска контейнеров.

---

## 4. Стратегия масштабирования (Рост системы в 1000 раз)

| Параметр             | Прототип          | Рост              | Продакшен           |
|----------------------|-------------------|-------------------|---------------------|
| Оркестрация          | Docker Compose    | K3s (Apache 2.0)  | Managed K8s         |
| Векторное хранилище  | Neo4j (native)    | Qdrant (отдельно) | Qdrant (кластер)    |
| Конфиг-хранилище     | SQLite            | Postgres          | etcd (Apache 2.0)   |
| Глоссарий            | YAML + SQLite     | Postgres          | Postgres            |
| Кэш                  | Нет               | Valkey (Redis)    | Valkey (кластер)    |
| Очередь ingestion    | Синхронно         | Redis / RabbitMQ  | Kafka               |
| Модель LLM           | Qwen 7B (1 GPU)   | Qwen 14B (1 GPU)  | Qwen 72B (multi-GPU)|
| Модель эмбеддингов   | bge-m3 (1 GPU)    | bge-m3 (1 GPU)    | bge-m3 (отдельный)  |
| GPU                  | 1 (поэтапно)      | 2 (параллельно)   | N (автоскейлинг)    |

---

## 5. Матрица критических рисков

Примечание: Ниже зафиксированы 6 критических инфраструктурных рисков текущей фазы.

| № | Риск                                               | Митигация                                            |
|---|----------------------------------------------------|------------------------------------------------------|
| 1 | GPU-конфликт памяти между bge-m3 и Qwen 7B на одной карте RTX 2070 Super | Жёсткое поочерёдное использование видеокарты через Docker Compose Profiles |
| 2 | OOM (Out of Memory) СУБД Neo4j на хост-машине с 16 ГБ ОЗУ | Ограничение JVM (max heap 1G, pagecache 512M) через параметры контейнера |
| 3 | Ложные склейки сущностей нормализатором на Ступени 1 (порог 0.92) | Двухступенчатая проверка: зона 0.75-0.92 отправляется на верификацию в ЛЛМ |
| 4 | Раздувание и замусоривание графа связями SIMILAR_TO | Повышенный порог косинуса (0.85) + обязательный вывод отчётов для ручного ревью |
| 5 | Логические противоречия и галлюцинации ЛЛМ на этапе Extraction | Работа Qwen 7B на низкой температуре (0.1) + тотальная семантическая проверка графа Cypher-запросами в Validator v2 до сохранения изменений |
| 6 | Vendor lock-in — привязка к конкретному поставщику инфраструктуры (новое в v5) | Слой адаптеров (ADR-012) позволяет заменить любой компонент инфраструктуры через runtime config без переписывания ядра |