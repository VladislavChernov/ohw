# Design: add-topology-adapters

## Слои и пространство имён провайдеров

Слоты адаптеров — из SSOT `namespaces.yaml` (namespace `adapters`):
`graph_store`, `vector_store`, `llm`, `embeddings`, `reranker`.

Канонический список **реализованных** провайдеров (фабрика, `catalog.py`):

| Слот | Провайдеры (реализованы в M2/M3.1) |
|------|-------------------------------------|
| graph_store  | `neo4j`, `inmemory` |
| vector_store | `neo4j`, `inmemory` |
| embeddings   | `deterministic` (bge — бандл 2) |
| reranker     | `noop` (bge-reranker — бандл 2) |
| llm          | `openai`, `fake` |

SSOT `namespaces.adapters` задаёт **целевые** значения (`bge_m3_service`, `ollama`,
`bge_reranker`) как эталон инфраструктуры; каталог `available` в топологии — только
реализованное множество (`catalog.py`), чтобы `PUT` сразу отклонял неподдерживаемое.

## Состояние топологии

- **База:** `infra_topology.yaml` (`providers:`) — неизменяемый источник.
- **Overrides:** SQLite-таблица `adapter_overrides(slot, provider, updated_at)` + `revision`
  (монотонно растёт при каждом `PUT`).
- **Эффективная карта** `/api/v1/config/adapters`: `base[slot]` заменяется override'ом,
  если есть. Резолв слота для потребителя: **карта топологии (явный слот) > env > дефолт**
  — топология задаётся оператором осознанно (`TOPOLOGY_URL`), поэтому её выбор провайдера
  перекрывает env; параметры соединений (NEO4J_URI, LLM_BASE_URL) при любом выборе из env.

## Контракт Topology API (:8005)

- `GET /api/v1/topology` → `{version, environment, network, providers, endpoints, startup}`
  (провайдеры — база из yaml без override'ов; полная картина инфраструктуры).
- `GET /api/v1/config/adapters` → `{revision, adapters: {slot: provider}}` (эффективная карта).
- `PUT /api/v1/config/adapters` (body `{slot: provider, ...}`): валидация слота/провайдера
  по `available`; успех → override записан, `revision++`, ответ `{revision, adapters}`;
  неизвестный слот/провайдер → **422** `{detail}`. Частичный body — обновляет только заданные слоты.
- `GET /api/v1/config/adapters/available` → `{slots: {slot: [providers]}}`.
- `/health` → `{status: "ok"}`.
- Аутентификация: заголовок `X-API-Key` == `AUTH_API_KEY` (единый `GRAPH_AUTH_API_KEY` стека);
  при несовпадении — 401. Формат — FastAPI-зависимость `require_api_key`.

## Потребление в Query Worker (hot-reload)

Сегодня `QueryWorker` строит `QueryPipeline` один раз в `main()` (`runtime.build_pipeline`,
env-фабрика). В M3:

1. Если `TOPOLOGY_URL` задана (compose `llm`-сервисы в стеке с профилем `topology`):
   стартовая карта = `fetch_adapter_map(TOPOLOGY_URL, key)` (fallback env при недоступности).
2. Воркер перед каждым опросом цикла проверяет `fetch_revision` (интервал
   `TOPOLOGY_POLL_INTERVAL`, дефолт 5 с). Смена revision → пересборка `QueryPipeline`
   из новой карты (провайдеры строятся заново; старые ресурсы (соединения Neo4j)
   закрываются через существующий API — у adapter-классов `close()` при наличии).
3. `TOPOLOGY_POLL_INTERVAL=0` → опрос отключён (один pipeline, как M2);
   отсутствие топологии → прежнее поведение env.

Это даёт «переключение без перезапуска» (L1-03): `PUT` на :8005, и воркер подхватывает
карту на следующей итерации цикла — без рестарта контейнера.

## Интеграция в compose

- `topology-orchestrator`: `build: {context: .., dockerfile: Dockerfile}`,
  `image: ohw/topology-orchestrator:prototype`, `command: ["graphrag-topology"]`,
  профиль `topology`, порт `8005:8005`, volume `../infra_topology.yaml:/topology/infra_topology.yaml:ro`,
  `INFRA_TOPOLOGY_PATH` + `AUTH_API_KEY`, healthcheck `/:health`, `volume topology_data:/data`.
- `query-worker`/`query-api` (профиль `llm`) получают `TOPOLOGY_URL=http://topology-orchestrator:8005`
  и `TOPOLOGY_POLL_INTERVAL=5`; при поднятом профиле `topology` воркер стартует с карты
  топологии, иначе фоллбэк env — стек `config+graph+ingestion+llm` продолжает работать
  без `topology` (обратная совместимость).

## Совместимость и границы

- Env-фабрика (`build_graph_store` и др.) сохраняет сигнатуры — существующие вызовы
  и тесты не ломаются; `build_adapters` — новая функция поверх регистра.
- `QueryPipeline` не меняется (провайдеры те же ABC). Hot-reload пересобирает объект,
  контракты ядра не трогаются.
- Ingestion API остаётся env-конфигом в этом бандле (переключение эмбеддера на стороне
  ингеста — бандл 2 с реальным bge-m3 сервисом; там же подключим топологию).
- SSOT `namespaces.yaml` не правится (каталог реализации в коде, эталон слотов в YAML).

## Тестируемость

- `catalog.py` + `factory.build_adapters` — чистые юниты.
- `topology_service.app` — FastAPI TestClient (auth-зависимость инъектируема).
- Worker hot-reload — `QueryWorker` инъектирует `RevisionSource` (клиент), тест гоняет
  фейковый притвор: `revision` не изменился → одна пересборка; изменился → следующая.
- Локальный прогон стека: curl-сценарий из proposal §Проверка.