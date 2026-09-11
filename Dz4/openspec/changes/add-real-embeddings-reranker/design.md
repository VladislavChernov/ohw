# Design: реальные эмбеддинги и реранкер (add-real-embeddings-reranker)

## Контекст

Бандл 2/3 M3. M2-фабрика выбор транспорта через env (`EMBEDDER`, `RERANKER`),
бандл 1/3 (add-topology-adapters) добавил карту топологии для runtime-переключения
Query Worker. `infra/config/namespaces.yaml` (namespace `adapters`) — SSOT целевых
провайдеров: `embeddings: bge_m3_service`, `reranker: bge_reranker`. Compose-блоки
`embeddings-service` (:8004) и `reranker` (:8006) существуют как заглушки.

## Решения

### D1. ML-зависимости — вне базового окружения

torch/sentence-transformers тяжёлые и не нужны тестам/базовому стеку.
`pyproject.toml`/`uv.lock` **не меняются** (иначе `uv sync --frozen` в CI и
`uv run` в тестах потребовали бы сети для новых пакетов). ML-пакеты ставятся
pip'ом **в `.venv` сервисного образа** в `Dockerfile.embeddings`/`Dockerfile.reranker`
(после `uv sync`). Модули делают lazy-`importlib` в `model.py` — импорт сервиса не
тянет torch, тесты сервисов работают на mock-провайдерах.

### D2. Сервис = FastAPI + инъецируемый провайдер

Каждый сервисный модуль `app.py` предоставляет `create_app(provider=None)`:
- без аргумента — провайдер строится из env (real- либо mock-режим);
- с аргументом — фиксированный провайдер для тестов (детерминизм, без env).

Mock-режим **явный** (`EMBEDDINGS_MOCK=true` / `RERANKER_MOCK=true`): детерминированные
результаты той же размерности, что и real (прото-инварианты L4-01/L2-04 на контуре).
Real-режим при недоступности модели/устройства отвечает 503 с понятным описанием
(не тихий деградация — размерность оси не ломается).

### D3. Эндпоинты

- Embeddings :8004 —
  `GET /health`; `POST /api/v1/embed` `{text, domain?} -> {vector, dimensions}`;
  `POST /api/v1/embed/batch` `{texts[], domain?} -> {vectors[], dimensions}`.
  Пустые тексты → 422. bge-m3: truncate `EMBEDDING_MAX_TOKENS` (по SSOT 8192),
  L2-нормализация, размерность `EMBEDDING_DIMENSIONS` (1024).
- Reranker :8006 —
  `GET /health`; `POST /api/v1/rerank` `{query, chunks[{id,text}]} -> {scores[]}`,
  скоры по индексу входного массива. Пустой query/chunks → 422.
  bge-reranker-base на CPU.

### D4. Адаптеры — HTTP-клиенты (стиль topology_client.py)

`retrieval/adapters/bge.py`:
- `BgeM3ServiceAdapter(Embedder)` — `embed(text, domain="")` → `POST /api/v1/embed`;
  несовпадение размерности/HTTP-ошибка → `RuntimeError` (fail-fast, pipeline упадёт
  явно);
- `BgeRerankerAdapter(Reranker)` — `rerank(query, chunks)` → `POST /api/v1/rerank`,
  возвращает `scores` как `list[float]` (длина = len(chunks)); ошибка → fail-fast.

Параметры соединения из env (конвенция factory): `EMBEDDINGS_URL`
(default `http://embeddings-service:8004`), `RERANKER_URL` (default
`http://reranker:8006`), `EMBEDDINGS_TIMEOUT_S`/`RERANKER_TIMEOUT_S`.

### D5. Каталог провайдеров

`ADAPTER_CATALOG["embeddings"]` += `"bge_m3_service"`, `["reranker"]` +=
`"bge_reranker"` — идентификаторы из SSOT namespace `adapters`. Topology
`GET /api/v1/config/adapters/available` считает каталог фабрики — расширится
автоматически. Дефолты (`deterministic`/`noop`) и M2-поведение env не меняются.

### D6. EmbedStage на адаптере (L2-04)

`EmbedStage(embedder: Embedder | None = None)`; `None` → `DeterministicEmbedder(dim=8)`
(текущее поведение и старые тесты L2-04 сохраняются). `run()`:
`meta["embedding"] = self._embedder.embed(chunk, ctx.domain)`.
`build_analyzer(registry, glossary_url, graph_store, vector_store, embedder=None)` и
`Executor(..., embedder=None)`; app.py резолвит `embedder = build_embedder()` — env
`EMBEDDER` выбирает провайдера (deterministic | bge_m3_service). Render того же
слота на query-стороне идёт через ту же фабрику — ось эмбеддингов едина (L2-04).

### D7. Compose/Docker

- `embeddings-service`: `build: {context: ., dockerfile: Dockerfile.embeddings}`,
  `command: ["graphrag-embeddings"]`; env `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`,
  `EMBEDDINGS_DEVICE`, `EMBEDDINGS_MOCK`, `AUTH_API_KEY`; существующие fallback'и
  (без сборки) не поддержены — запуск по требованию (профиль `embeddings`, L4-01).
- `reranker`: порт `8006:8006`, `build`+`command`, healthcheck; профиль `reranker`.
- База `infra_topology.yaml` остаётся `embeddings: deterministic`, `reranker: noop`
  (стек без GPU из коробки); переключение на bge — операторский шаг runbook
  (правка providers + `EMBEDDER` у ingestion).
- `docs/04` §1 таблица портов: добавить Reranker Service :8006.

## Риски

- **Сеть/GPU недоступны при live-приёмке** — падаем к mock-контуру (BR-7):
  поднимаются образы (без ML-пакетов? нет — с ними build падает) →
  компромисс: при невозможности build боевых образов live-приёмка выполняется
  mock-режимом в compose медленнее нельзя — используем сервисные образы из
  базового Dockerfile (без ML) с `EMBEDDINGS_MOCK/RERANKER_MOCK=true`; шаг
  реального прогона фиксируется в runbook.
- **Раздувание образа** — боевой embeddings-образ ~5+ ГБ (torch-cuda + модель 2.3 ГБ).
  Приемлемо для фазы индексации (L4-01), volumes `models_data` для кэша HF.
- **Размерность** — mock и real строго `EMBEDDING_DIMENSIONS`; Transport
  аккуратен, чтобы консистентность L2-04 не зависела от режима.