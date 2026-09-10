# Proposal: Topology Orchestrator (:8005) + runtime-переключение адаптеров (PUT /config/adapters)

> Бандл 1/3 вехи M3. Продолжение ДЗ4 после `add-retrieval-graph-toggle` (`93b96c3`).
> Строится на M2: фабрика адаптеров `retrieval/adapters/factory.py` читает env
> (`GRAPH_STORE`, `EMBEDDER`, ...), `infra_topology.yaml` и namespace `adapters` в
> SSOT (`infra/config/namespaces.yaml`) объявлены, но **не читаются кодом**.
> Этот бандл реализует Topology Orchestrator Service (:8005, профиль `topology`,
> ADR-019) и runtime-переключение адаптеров «на лету» без правки ядра (L1-03).
> Топология сети (docs/00, DOC: infra_topology.yaml) — источник правды для
> активных провайдеров.

## Почему

Фабрика провайдеров привязана к env и собирается один раз при старте процесса.
Инфраструктурный контур (ADR-019: «Topology Orchestrator — отдельный сервис
:8005») задекларирован в compose/доках, но не реализован: образ `ohw/topology-service`
— заглушка без `build:`, `GET/PUT /api/v1/config/adapters` из `docs/04` §3
не существуют. Переключение оси (граф/вектор/LLM) — только правкой env и
рестартом контейнера. Нет каталога «какие адаптеры доступны» и мониторинга
«какие активны сейчас».

## BR (бизнес-требования; строки `docs/prototype_requirements.md`, веха 3)

- **BR-1. Topology Orchestrator Service (:8005).** Отдельный сервис (ADR-019),
  профиль `topology`: читает `infra_topology.yaml`, отдаёт текущую топологию
  и активную карту адаптеров через REST, управляет runtime-переключением.
- **BR-2. Adapters Management API (`docs/04` §3).** `GET /api/v1/config/adapters` —
  текущие адаптеры; `PUT /api/v1/config/adapters` — переключение на лету
  (валидация по каталогу, персист в SQLite); `GET /api/v1/config/adapters/available` —
  список реализованных провайдеров. Путь сохранён из v5-доков, хост — Topology :8005 (ADR-019).
- **BR-3. Потребление карты адаптеров.** Query Worker строит провайдеры по карте
  из Topology; hot-reload: при смене версии карты (revision) pipeline пересобирается
  без рестарта контейнера. Env остаётся фоллбэком для локальных запусков/тестов
  (совместимость с M2).
- **BR-4. Каталог = реализация, а не прожект.** `available` отражает фактически
  реализованные провайдеры (регистр фабрики). Реальные bge-провайдеры (bge-m3,
  bge-reranker) добавятся бандлом 2/3; здесь — `deterministic`, `neo4j`, `inmemory`,
  `openai`, `fake`, `noop`.
- **BR-5. Автономность.** `uv run pytest -q` зелёно, `ruff`, `mypy` чисто; e2e-маркер не трогаем.

## Что делаем

- **`src/graphrag_proto/topology_service/`** — новый сервис:
  - `topology.py` — загрузка/валидация `infra_topology.yaml` (структура, известные слоты);
  - `store.py` — SQLite-хранилище override'ов адаптеров (PUT) + revision;
  - `catalog.py` — каталог реализованных провайдеров (общий с `adapters/factory.py`);
  - `app.py` (FastAPI :8005): `GET /api/v1/topology`, `GET /api/v1/config/adapters`,
    `PUT /api/v1/config/adapters`, `GET /api/v1/config/adapters/available`, `/health`,
    проверка `X-API-Key`;
  - console script **`graphrag-topology`**.
- **`src/graphrag_proto/retrieval/adapters/factory.py`** — регистр провайдеров
  (слоты `graph_store`, `vector_store`, `embeddings`, `reranker`, `llm`) +
  `build_adapters(adapter_map)` — сборка всех провайдеров по карте; существующие
  `build_*` остаются и делегируют регистру.
- **`src/graphrag_proto/retrieval/adapters/topology_client.py`** — тонкий клиент
  к Topology: `fetch_adapter_map(url, api_key)` + `fetch_revision`.
- **`src/graphrag_proto/query_service/runtime.py`** — при `TOPOLOGY_URL`: карта
  адаптеров поверх env; `QueryWorker` получит hot-reload (опрос revision,
  пересборка `QueryPipeline`).
- **`src/graphrag_proto/query_service/worker.py`** — периодический опрос revision
  (интервал `TOPOLOGY_POLL_INTERVAL`, по умолчанию 5 с) и пересборка pipeline.
- **`infra/compose.yaml`** — сервису `topology-orchestrator`: `build:`,
  `command: ["graphrag-topology"]`, healthcheck, volume `infra_topology.yaml`,
  порт `8005:8005`; профиль `topology` остаётся отдельным (ADR-019). Запуск
  сервиса — по требованию оператора.
- **`pyproject.toml`** — script `graphrag-topology`.
- **`tests/test_topology_service.py`** (+ мок-клиент в `test_retrieval_*`): загрузка
  yaml, валидация PUT, каталог, hot-reload воркера (смена revision → пересборка).
- **`docs/demo_runbook.md`** — раздел «Topology: переключение адаптеров» (curl-сценарий);
  `docs/04 §3` — пометка о хосте :8005.

## Спека

- `specs/add-topology-adapters/spec.md` — «Delta Spec»: контракт Topology API,
  схема карты адаптеров, приоритет конфигурации, hot-reload воркера (по BR-1..BR-5).

## Что НЕ входит (смежные бандлы)

- Реальные провайдеры bge-m3 сервиса и bge-reranker — бандл 2; семантический кэш — бандл 3.
- Topology UI (:8502, Streamlit) — опциональная веха M5 (ADR-019).
- Переключение адаптеров на стороне Ingestion API — остаётся env-конфигом (M2).
- Правка SSOT `namespaces.yaml`: namespace `adapters` остаётся эталонным набором слотов
  и целевых значений; каталог реализаций живёт в фабрике.

## Проверка

1. `uv run pytest -q` — зелёно; `uv run ruff check .`, `uv run mypy src` — чисто.
2. Подъём стека с профилем `topology`; `curl :8005/api/v1/config/adapters` — карта из yaml.
3. `curl -X PUT .../api/v1/config/adapters -d '{"vector_store":"inmemory"}'` →
   200, карта обновилась; `revision` вырос; query-worker (без рестарта) перестроил pipeline
   (проверка unit-тестом + прогон запроса на стеке).
4. `curl .../api/v1/config/adapters/available` — реализованные провайдеры;
   `PUT` с неизвестным провайдером → 422.