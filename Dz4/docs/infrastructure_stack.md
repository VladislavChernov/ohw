# Документация: Технологический стек и инфраструктура (Infrastructure Stack)

> **Версия:** v6 (итерация поверх базы v5)
> **Последнее обновление:** 2026-09-05
>
> Справочник: сводит воедино разбросанные факты об окружении — контейнеры, языки, порты,
> внешние системы/СУБД, compose-профили, модели и лицензии. Детали остаются в источниках:
> порты и API — `docs/04_services_config.md`, ops/профили/масштабирование — `docs/06_operations_and_risks.md`,
> адаптеры — `docs/adapters_specification.md`, контракты API — `docs/api_reference.md`.

## 1. Контуры и языки реализации

Архитектурно система делится на контуры, связанные **языко-независимыми контрактами**
(gRPC/Proto3, JSON-Schema; v6 — `docs/history.md`, Этап 6; JSON-Schema стриминга — ADR-016).
Контракты на-проводные, поэтому **выбор языка контура — решение реализации, а не
архитектурное свойство**: веб-слой обязан соблюдать контракты `docs/api_reference.md`,
`docs/05_adr_log.md` (ADR-016/017/018), а конкретный язык — на усмотрение владельца
(процедура замены — `docs/web_layer_replacement.md`).

| Контур | Состав | Язык (решение прототипа, ADR-020) |
|--------|--------|------------------------------------|
| Сетевой контур | Query API Gateway, Query Workers Pool, обслуживание WebSockets | Python (прототип); компилируемый язык без GIL — целевой вариант фазы 2 |
| ИИ и данные | Config/Ingestion/Glossary/Embeddings Service, Topology Orchestrator Service, Ingestion Pipeline, Topology UI, Web UI — Конфигуратор | Python (CUDA-инференс, канонизация, Cypher-валидация) |

## 2. Карта контейнеров и сервисов

Порты — в сети `ohw_net` (`docs/04` §1); Docker Compose-профили — `docs/06` §3.

| Контейнер / сервис | Роль | Язык | Порт | Профиль | Источник |
|--------------------|------|------|------|---------|----------|
| Query API Gateway (вкл. MCP-шлюз) | Асинхронный вход: API-ключ, `task_id`, маршрутизация в очередь, стриминг; MCP-интеграция ИИ-агентов (протокол MCP/JSON-RPC) | Python (FastAPI) — прототип (ADR-020) | 8000 | llm | docs/00 (метка «Query API Gateway / MCP-шлюз :8000», актор «ИИ-агент через MCP»), history.md (Этап 6), docs/04 §1, docs/05_adr_log.md ADR-020, docs/web_layer_replacement.md |
| Web UI — Конфигуратор (Streamlit) | Веб-панель «Бизнес-онтология»: Domain Profile, глоссарии; фронтенд к Config API | Python (Streamlit) | 8501 | config | docs/00 (актор «Web-UI Config Service»), CONCEPT §6.4, docs/04 §2–§3 |
| Query Workers Pool | Оркестрация раунда поиска через слой адаптеров | Python — прототип (ADR-020) | — (внутренний) | — | docs/00 (сетевой контур), history.md (Этап 6), docs/05_adr_log.md ADR-020, docs/web_layer_replacement.md |
| Runtime Query Queue (Valkey / Redis Streams) | Очередь задач и кэш | сторонний сервис | — (внутренний, 6379) | — | docs/00, docs/06 §4 (Кэш), history.md (Этап 6) |
| Config Service | Domain Profile, `namespace: adapters` | Python | 8001 | config | docs/04 §1–§3 |
| Topology Orchestrator Service | Фабрика провайдеров по `prototype/infra_topology.yaml`, runtime-переключение адаптеров (отдельный сервис, ADR-019) | Python | 8005 | topology | ADR-019, docs/00 |
| Topology UI (Streamlit) | Настроечное приложение «Топология инфраструктуры» (оператор, ADR-019); фронтенд к Topology Orchestrator Service | Python (Streamlit) | 8502 | topology | ADR-019, docs/00 |
| Ingestion API | Приём файлов/URL, управление джобами индексации | Python | 8002 | ingestion | docs/04 §1, docs/02 §1 |
| Ingestion Pipeline Workers | 9 этапов: CHUNK…VALIDATE, канонизация, COMMIT | Python | — | ingestion | docs/02 §1, docs/06 §3 (профиль ingestion) |
| Glossary Service | Словари доменов, трансляция тегов → канонический ряд | Python | 8003 | config | docs/04 §4, docs/00 |
| Embeddings Service | Расчёт векторов bge-m3 (GPU) | Python | 8004 | embeddings | docs/04 §1, docs/02 §1 (EMBED) |
| Neo4j Community | Граф + нативный векторный индекс (ADR-001) | Java (сторонний) | 7687 (Bolt) / 7474 (HTTP) | graph | docs/04 §1, docs/06 §3 |
| Ollama Server | Инференс Qwen 2.5 7B Instruct (GPU) | Go (сторонний) | 11434 | llm | docs/04 §1 |
| bge-m3 (LocalSentenceTransformerAdapter) | Встроенный эмбеддер без отдельного контейнера | Python | — | — | docs/02 §1, adapters_specification.md |
| bge-reranker-base | Реранкер (CPU); NoOpRerankerAdapter — отключить | Python | — | reranker | docs/03 §1, docs/06 §3 |
| Prometheus / Grafana / Loki (+Promtail) | Наблюдаемость, метрики `/metrics`, JSON-логи | сторонние | — | monitoring | docs/06 §2 |
| DCGM Exporter | Мониторинг VRAM/температуры GPU | сторонний | — | monitoring | docs/06 §2 |

## 3. Внешние системы и СУБД по осям и фазам

| Ось | Прототип | Рост | Продакшен | Интерфейс адаптера |
|-----|----------|------|-----------|--------------------|
| Графовая БД | Neo4j (native) | Neo4j / Memgraph | Memgraph / кластер | `GraphStoreProvider` |
| Векторная БД | Neo4j native vector index | Qdrant | Qdrant (кластер) | `VectorStoreProvider` |
| Хранилище конфигов | SQLite | Postgres | etcd | Config Service |
| Глоссарий | YAML + SQLite | Postgres | Postgres | Glossary Service |
| Кэш / очередь задач | — | Valkey (Redis) | Valkey (кластер) | Runtime Query Queue |
| Очередь ingestion | синхронно | Redis / RabbitMQ | Kafka | Ingestion |
| LLM | Qwen 2.5 7B (1 GPU) | Qwen 14B (1 GPU) | Qwen 72B (multi-GPU) | `LLMInference` |
| Embeddings | bge-m3 (1 GPU) | bge-m3 (1 GPU) | bge-m3 (отдельный) | `Embedder` |
| Reranker | bge-reranker-base (CPU) | — | — | `Reranker` |
| Оркестрация | Docker Compose | K3s | Managed K8s | — |

Базовые реализации и альтернативы по каждому интерфейсу — в `docs/adapters_specification.md`
и `history.md` (Этап 5).

## 4. Docker Compose профили (управление GPU/OЗУ)

| Профиль | Контейнеры | Когда |
|---------|------------|-------|
| `config` | Config Service + Glossary Service + Web UI — Конфигуратор | всегда |
| `graph` | Neo4j Community | всегда |
| `topology` | Topology Orchestrator Service + Topology UI | по требованию оператора (ADR-019) |
| `embeddings` | Embeddings Service (bge-m3) | фаза индексации |
| `ingestion` | Ingestion API + скрипты пайплайна | поочерёдный захват GPU |
| `llm` | Query API + Ollama (Qwen 7B) | фаза поиска |
| `reranker` | bge-reranker-base (CPU) | опционально, фаза поиска |
| `monitoring` | Prometheus + Grafana + Loki | по требованию |

Профиль Docker ≠ Domain Profile: домен — конфигурация (YAML), его смена не требует перезапуска
контейнеров (`docs/06` §3).

## 5. Модели и лицензии

| Компонент | Версия/параметры | Лицензия источников |
|-----------|------------------|---------------------|
| Qwen 2.5 7B Instruct | LLM `extraction`/`llm` (temp 0.1/0.3) | Apache 2.0 |
| bge-m3 | Embeddings, 1024 dim | см. дистрибутив BGE |
| bge-reranker-base | CPU-реранкер | см. дистрибутив BGE |
| Neo4j Community | лимит JVM 1.5 ГБ (heap 1G, pagecache 512M) | GPLv3 |
| K3s | фаза «Рост» | Apache 2.0 |
| Valkey / Qdrant / Memgraph / RabbitMQ / Kafka | альтернативы осей | лицензии не зафиксированы в доках |

## 6. Сеть, хранилища и GPU

- **Сеть:** изолированная Docker-сеть `ohw_net`; сервисы общаются по именам контейнеров (`docs/04` §1).
- **Volumes:** сохраняются веса LLM/моделей и индексы графа; `docker compose down` их не удаляет
  (README §3, шаг «Остановка»).
- **GPU:** 1× NVIDIA RTX 2070 Super (8 ГБ VRAM); bge-m3 и Qwen 7B работают **поочерёдно**
  через compose-профили (риск №1 в `docs/06` §5). Минимальные требования — README §1.

## 7. Где что читать

| Вопрос | Документ |
|--------|----------|
| Порты и сеть | `docs/04_services_config.md` §1 |
| Профили и масштабирование | `docs/06_operations_and_risks.md` §3–§4 |
| Метрики/наблюдаемость | `docs/06_operations_and_risks.md` §2 |
| Реализации адаптеров | `docs/adapters_specification.md`, `history.md` Этап 5 |
| API контракты контейнеров | `docs/api_reference.md` |