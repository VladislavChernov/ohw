# Документация: Требования к прототипу (Prototype Requirements)

> **Версия:** v1 (стартовая редакция)
> **Последнее обновление:** 2026-09-05
>
> Единый документ для фазы прототипирования: цель и границы прототипа, скоуп,
> системные требования, набор фиксированных контрактов, состав работ по вехам,
> критерий готовности (eval-гейт), а также заглушки User Guide и Runbook,
> заполняемые в ходе реализации.

## 1. Цель прототипа

**Проверить на практике утверждения технической документации (v5–v7):**

1. Что гибридный подход «граф + вектор» даёт измеримый прирост качества ответов по
   сравнению с vector-only (ADR-015, метрики recall/coverage/groundedness).
2. Что ядро доменно-агностично: активация другого Domain Profile (it / library / cinema)
   работает без изменений кода и без перезапуска контейнеров (инвариант L1-01, L1-03).
3. Что слой адаптеров изолирует ядро от конкретных технологий: смена реализации
   (`graph_store`, `vector_store`, `llm`, `embeddings`, `reranker`) через runtime config
   не требует правки ядра (L1-02, L1-03).
4. Что пайплайн из 9 этапов (INGEST → … → COMMIT) реально исполним на целевом железе с
   учётом GPU-гейтинга (поочерёдная работа эмбеддингов bge-m3 и LLM Qwen 7B — инвариант L4-01).

Прототип — **не цель, а валидация**: любые расхождения с документацией фиксируются и
возвращаются либо в реализацию, либо в ADR (запись нового решения).

## 2. Границы (что НЕ входит в прототип)

| Исключено | Причина | Куда уходит |
|-----------|---------|-------------|
| Каскадная очередь ingestion (Kafka/RabbitMQ) | Прототип — синхронная схема; очередь — фаза роста | `docs/06`, `infrastructure_stack` §3 |
| Reconciliation с TMS/GitLab | Операционная задача, не критична для валидации | `operations_requirements.md` §3 |
| Eval-инфраструктура с LLM-as-judge | Метрики groundedness требуют доработки judge-шага | ADR-015 §5, `operations_requirements.md` §5 |
| Multi-GPU / кластеры (K3s/K8s, Qdrant-кластер, Memgraph) | Фаза «Рост», после прототипа | `infrastructure_stack` §3 |
| Web UI конфигуратор / Topology UI | Нужен для UX-валидации лишь на поздних вехах; для API-валидации достаточно curl/WebSocket-клиента. Topology UI — отдельное приложение по ADR-019 | `docs/04` §1, CONCEPT §6.4, `docs/05_adr_log.md` ADR-019 |
| MCP-шлюз | ADR-017 зафиксирован, но интеграция с ИИ-агентами — отдельная веха | ADR-017 |

Скоуп формируется по принципу «минимально достаточной демонстрации» каждого утверждения
из §1. Не входящее в скоуп не реализуется и не тестируется на этом этапе.

## 3. Системные требования

### 3.1. Минимальное окружение (целевое)

| Ресурс | Требование | Примечание |
|--------|-----------|------------|
| ОС | Linux (Ubuntu 22.04+), WSL2 (Windows 11), macOS (M1/M2/M3) | — |
| CPU | 4 ядра (мин.), 8 ядер (реком.) | Реранкинг bge-reranker-base на CPU |
| RAM | 16 ГБ | Neo4j ограничен 1.5 ГБ JVM |
| GPU | NVIDIA, 8 ГБ VRAM (RTX 2070 Super и выше) | bge-m3 + Qwen 7B поочерёдно (L4-01) |
| Диск | ~20 ГБ свободного (веса моделей + индексы) | Volumes сохраняются между запусками |
| Docker | Engine v24.0+, Compose v2.20+ | — |
| CUDA | Поддержка в Docker (`--gpus all` работоспособен) | Проверка — `docker run --rm --gpus all ...` |

### 3.2. Целевое железо прототипа (фиксированное)

- 1× NVIDIA RTX 2070 Super, 8 ГБ VRAM.
- bge-m3 и Qwen 2.5 7B работают **поочерёдно** через compose-профили
  (`embeddings` / `llm`), инвариант L4-01, риск №1 (`docs/06` §5).

## 4. Стек прототипа (фиксированный набор на этап)

| Ось | Выбранная реализация | Интерфейс адаптера |
|-----|----------------------|--------------------|
| Граф | Neo4j Community (native vector index, ADR-001) | `GraphStoreProvider` |
| Векторы | Neo4j native vector index | `VectorStoreProvider` |
| Конфиги/глоссарий | SQLite + YAML-профили | Config / Glossary Service |
| LLM | Ollama + Qwen 2.5 7B Instruct (`qwen2.5:7b-instruct`) | `LLMInference` |
| Embeddings | bge-m3, 1024 dim (Embeddings Service :8004 или LocalSentenceTransformerAdapter) | `Embedder` |
| Reranker | bge-reranker-base (CPU); может быть отключён (NoOpRerankerAdapter) | `Reranker` |
| Оркестрация | Docker Compose, сеть `ohw_net` | — |
| Очередь запросов | Valkey / Redis Streams (сетевой контур, v6) | — |
| Сетевой контур (Query API Gateway :8000, Query Workers) | **Python 3.11 + FastAPI** на прототипе (ADR-020); целевая замена на Go/Rust — фаза 2, процедура в `docs/web_layer_replacement.md` | — |
| Топология | Topology Orchestrator Service (:8005) + Topology UI (:8502) — отдельный сервис и приложение (ADR-019) | — |

Полная карта контейнеров, портов, профилей и лицензий — `docs/infrastructure_stack.md`.
Портовая карта конкретной инсталляции (`8000–8005`, `8501–8502`, `7687/7474`, `11434`) —
`docs/04_services_config.md` §1; консервированный снапшот конкретики v5 —
`docs/history/v7/CONCEPT.md`.

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

### Веха 0 — Инфраструктура (основа)
- [ ] Docker Compose: профили `config`, `graph` (Neo4j, ограничение JVM), сеть `ohw_net`.
- [ ] Проверка GPU через Docker, каталог volumes для весов моделей.
- [ ] Config Service: SQLite + загрузка Domain Profile (YAML), endpoints `docs/04` §2.
- [ ] Glossary Service: `glossary.{profile}.yaml`, RESOLVE/VALIDATE (стек proto: SQLite).
- [ ] Topology Orchestrator Service (:8005) + профиль `topology` (ADR-019).

### Веха 1 — Ingestion Pipeline (9 этапов)
- [ ] Ingestion API (:8002): POST /documents, GET/DELETE /jobs/{id} (ADR-018).
- [ ] Этап INGEST→COMMIT: CHUNK (512/64), EMBED (bge-m3, batch 32), EXTRACT (Qwen),
      NORMALIZE (v3, fallback), DEDUP (0.92/0.75/0.85), CONTRACT, VALIDATE, COMMIT.
- [ ] Document Registry + версии источника (ADR-014), идемпотентность по content hash.
- [ ] Семейство «запуск профилей embeddings/ingestion поочерёдно» (L4-01).

### Веха 2 — Query API (асинхронный контур)
- [ ] Query API (:8000): POST /query → 202, GET /query/tasks/{task_id}.
- [ ] Query Workers + Task Queue (Valkey/Redis Streams).
- [ ] Стриминг WebSockets/SSE: контракт ADR-016.
- [ ] Retriever: Graph (Cypher-шаблон) ∥ Vector + Reranker + Context Assembly (4096, вытеснение).

### Веха 3 — Адаптеры и runtime-переключение
- [ ] Интерфейсы-адаптеры: GraphStoreProvider, VectorStoreProvider, LLMInference, Embedder, Reranker.
- [ ] Реализации-кандидаты из снапшота v7 (§2): Neo4jGraphStore, Neo4jVectorStore,
      OllamaAdapter, BgeM3ServiceAdapter / LocalSentenceTransformerAdapter, BgeRerankerAdapter.
- [ ] `PUT /api/v1/config/adapters` — переключение оси на лету через Topology Orchestrator
      Service без перезапуска (L1-03, ADR-019).
- [ ] Несколько профилей доменов (it / library / cinema) + переключение активацией (L1-01).

### Веха 4 — Eval и гейт готовности
- [ ] Eval-датасет `prototype/infra/eval/{domain}/questions.jsonl` (мин. 50 вопросов/домен для базового среза).
- [ ] Метрики Retrieval@K=5, генерации (groundedness/coverage), lift-отчёт (ADR-015).
- [ ] Baseline (vector-only) vs target (hybrid) — критерий готовности прототипа (§8).

### Веха 5 (опционально/отдельным решением) — MCP-шлюз и UI
- [ ] MCP-шлюз (:8000, JSON-RPC), инструменты ADR-017, rate-limiting (`docs/security.md` §4).
- [ ] Streamlit — конфигуратор «Бизнес-онтология» (:8501) — отдельное приложение.
- [ ] Streamlit — Topology UI «Топология инфраструктуры» (:8502) — отдельное приложение (ADR-019).

## 7. Валидационные проверки по инвариантам

| Инвариант | Проверка на прототипе |
|-----------|-----------------------|
| L1-01 домен-агностичность | Активация it → library → cinema без изменений кода |
| L1-02/L1-03 интерфейсы и runtime config | Смена `vector_store` на Qdrant / `llm` на OpenAICompatible — работает без правки ядра |
| L1-05 детерминизм | Повторный прогон NORMALIZE/DEDUP даёт тот же результат |
| L2-01 canonical_name | Констрейнт в Neo4j, отсутствие дублей после DEDUP |
| L2-04 атомарность COMMIT | Ошибка на этапе COMMIT → граф без частичных записей |
| L3-01..L3-05 | Выполнение пайплайна и контрактов (журнал этапов, лимит контекста, 202+task_id) |
| L4-01 GPU поочерёдность | Профили embeddings/ingestion не конфликтуют по VRAM |
| L5-01..L5-04 | X-API-Key во всех контурах; redaction секретов; 401 на неверный ключ |

## 8. Критерий готовности прототипа (объявляется по результатам)

Прототип считается **валидированным**, когда:

1. Пайплайн исполним на целевом железе без ручного вмешательства (один вызов Ingestion API
   проходит все 9 этапов до COMMIT).
2. Query API: вопрос через асинхронный контур возвращает ответ + sources + тайминги
   (стриминг по контракту ADR-016).
3. Переключение Domain Profile работает без перезапуска контейнеров.
4. **Eval-гейт:** groundedness и coverage не ниже baseline (vector-only) при приемлемой
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
| Материал конкретики v5 (реализации, порты, тайминги) | `docs/history/v7/CONCEPT.md` |
| Стек, порты, профили, модели | `docs/infrastructure_stack.md`, `docs/04_services_config.md` |
| Контракты API и события | `docs/api_reference.md`, ADR-016/017/018 |
| Инварианты и чек-лист ревью | `docs/invariants.md` |
| Eval-методология | `docs/05_adr_log.md` ADR-015 |
| Запуск/остановка, системные требования | корневой `README.md` §1, §3, §5 |
| Эксплуатация (вне прототипа) | `docs/operations_requirements.md` |