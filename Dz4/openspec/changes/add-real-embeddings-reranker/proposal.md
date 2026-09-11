# Proposal: реальные эмбеддинги (bge-m3, :8004) и реранкер (bge-reranker, :8006)

> Бандл 2/3 вехи M3. Продолжение после `add-topology-adapters` (`dfb7e9f` / `81490af`,
> бандл 1/3) и закрытия хвостов (`2700199`).
> М2-фабрика реализует слот `embeddings` провайдером `deterministic` (8-мерный хэш,
> без GPU — L4-01), слот `reranker` — `noop`. Реальные провайдеры существовали только
> в SSOT (`infra/config/namespaces.yaml`: `embeddings: bge_m3_service`,
> `reranker: bge_reranker`) и в каталоге прожекта — кода не было.

## Почему

- Слот `deterministic` (8-dim хэш) не даёт семантики: косинусный поиск по хэш-векторам
  ≈ случайный. Гибридный retrieval (L2-03) требует реальных эмбеддингов.
- `ingestion_service/pipeline/orchestrator.py::EmbedStage` хардкодит
  `deterministic_embedding()` и **не ходит через адаптерный слой** — нарушение
  инварианта L2-04 (consistency ingest/query) при переходе на bge.
- Compose-блоки `embeddings-service` (:8004, GPU) и `reranker` (CPU) — заглушки без
  `build:`/`command`/порта; namespace `adapters` в SSOT и `infra_topology.yaml`
  остаются нереализуемыми без кода сервисов.
- Тяжёлые ML-зависимости (torch/sentence-transformers) не должны попадать
  в базовый образ и в окружение `uv run pytest` — изоляция сервисными образами.

## BR (строки `docs/prototype_requirements.md`, веха 3 — M3)

- **BR-1. Embeddings Service (:8004, GPU-профиль).** Отдельный сервис bge-m3
  (ADR-012/ADR-019): `POST /api/v1/embed`, `POST /api/v1/embed/batch`,
  `GET /health`. Explicit `mock`-режим (заглушка) для запуска без GPU/модели;
  реальная модель — lazy-загрузка `SentenceTransformer("BAAI/bge-m3")`, L2-нормализация,
  размерность из `EMBEDDING_DIMENSIONS` (по умолчанию 1024).
- **BR-2. Reranker Service (:8006, CPU-профиль).** Отдельный сервис
  bge-reranker-base: `POST /api/v1/rerank`, `GET /health`; скоры выровнены по
  индексу входных чанков. Опциональность (NoOp допустим) сохраняется.
- **BR-3. Адаптеры-клиенты.** `BgeM3ServiceAdapter(Embedder)` и
  `BgeRerankerAdapter(Reranker)` в `retrieval/adapters/bge.py` — HTTP-клиенты
  сервисов (стиль `topology_client.py`); параметры соединения — env.
- **BR-4. Каталог = реализация.** `ADAPTER_CATALOG`: `embeddings` += `bge_m3_service`,
  `reranker` += `bge_reranker`; `available` Topology автоматически расширится.
  Провайдеры bge-сервисов инстанцируются только при выборе (lazy, без ML-импортов).
- **BR-5. EmbedStage переводится на адаптер.** `EmbedStage` принимает `Embedder`
  (по умолчанию `DeterministicEmbedder`, dim=8 — M2-поведение для тестов и обычных
  прогонов); `build_analyzer` собирает эмбеддер из `build_embedder()` (env
  `EMBEDDER`). Ось эмбеддингов ingest/query идёт через один каталог провайдеров (L2-04).
- **BR-6. Инварианты.** M2/M3: для `embeddings=deterministic` векторы чанков и запроса
  идентичны прежним (тесты L2-04 не меняются); автономный прогон без стека —
  `uv run pytest -q` зелёно, `ruff`, `mypy` чисто.
- **BR-7. Лив-приёмка «гибкая».** При доступности сети/GPU/модели — подъём боевых
  образов (Dockerfile.embeddings /.reranker) и прогон с реальными bge-провайдерами;
  иначе — фиксация шагов в runbook + live-проверка контура в `mock`-режиме (wiring,
  консистентность, композ).

## Что делаем

- **`src/graphrag_proto/embeddings_service/`** — новый сервис:
  - `model.py` — lazy-загрузка bge-m3 (SentenceTransformer, `EMBEDDINGS_DEVICE`), провайдер
    эмбеддинга с mock-режимом (`EMBEDDINGS_MOCK=true`): нормализованный
    детерминированный вектор размерности `EMBEDDING_DIMENSIONS`;
  - `app.py` (FastAPI :8004): `/health`, `POST /api/v1/embed`,
    `POST /api/v1/embed/batch` (422 при пустом тексте), инъекция провайдера для тестов;
  - console script **`graphrag-embeddings`**.
- **`src/graphrag_proto/reranker_service/`** — новый сервис:
  - `model.py` — lazy-загрузка CrossEncoder `BAAI/bge-reranker-base` (CPU), mock-режим
    (`RERANKER_MOCK=true`, скор по пересечению токенов);
  - `app.py` (FastAPI :8006): `/health`, `POST /api/v1/rerank` (скоры по индексам
    входных чанков);
  - console script **`graphrag-reranker`**.
- **`retrieval/adapters/bge.py`** — `BgeM3ServiceAdapter` (`embed`) и
  `BgeRerankerAdapter` (`rerank`); недоступность сервиса/неверная размерность → явная
  ошибка (fail-fast, не тихий фоллбэк).
- **`retrieval/adapters/factory.py`** — `ADAPTER_CATALOG` (два новых провайдера),
  `_build_embedder`/`_build_reranker` для `bge_m3_service`/`bge_reranker`; env:
  `EMBEDDINGS_URL` (default `http://embeddings-service:8004`),
  `RERANKER_URL` (default `http://reranker:8006`), timeouts `*_TIMEOUT_S`.
- **`ingestion_service`** — `EmbedStage(embedder=None)` → по умолчанию
  `DeterministicEmbedder`; `Executor`/`build_analyzer` принимают `embedder`
  (собирается `build_embedder()` по env `EMBEDDER`).
- **`infra/compose.yaml`** — `embeddings-service`: `build:` (Dockerfile.embeddings),
  `command: ["graphrag-embeddings"]`; `reranker`: `build:` (Dockerfile.reranker),
  `command: ["graphrag-reranker"]`, `ports: ["8006:8006"]`, healthcheck.
- **`Dockerfile.embeddings` / `Dockerfile.reranker`** — базовый образ + pip-установка
  ML-пакетов поверх `.venv` (uv), без правки `pyproject`/`uv.lock` (тестовое окружение
  не раздувается).
- **`infra_topology.yaml`** — обновить комментарий (провайдеры реализованы); базовые
  providers остаются `deterministic`/`noop` (запуск без GPU из коробки). Переключение
  на bge — через runbook (профили + правка providers).
- **`docs/demo_runbook.md`** — раздел «Реальные эмбеддинги и реранкер (M3.2)»:
  подъём профилей `embeddings`+`reranker`, переключение топологии, live-проверка,
  L4-01 фазирование; **`docs/04`** — порт :8006 в таблице.
- **Тесты**: `tests/test_embeddings_service.py`, `tests/test_reranker_service.py`
  (mock-провайдер, 422), `tests/test_bge_adapters.py` (стаб-HTTP-серверы),
  расширение `test_factory_adapters.py` (каталог + сборка) и
  `test_ingestion_api.py`/`test_commit_stage.py` (EmbedStage пишет вектор
  инжектированного эмбеддера).

## Спека

- `specs/add-real-embeddings-reranker/spec.md` — контракты обоих сервисов, схема
  env-параметров, поведение адаптеров (fail-fast), wiring EmbedStage, что НЕ меняется
  (по BR-1..BR-7).

## Что НЕ входит (смежные бандлы)

- Семантический кэш (Valkey) — бандл 3.
- Переключение адаптеров на стороне Ingestion API через Topology — остаётся
  env-конфигом (M2); hot-reload — только Query Worker (бандл 1).
- Коннекторы внешних источников — веха M6 (план: `add-source-connectors`).
- Batch-эмбеддинг в `EmbedStage` (эндпоинт `/embed/batch` предоставляется, пайплайн
  использует последовательный `embed` — оптимизация вне бандла).

## Проверка

1. `uv run pytest -q` зелёно; `uv run ruff check .`, `uv run mypy src` — чисто.
2. Тесты сервисов (mock-режим) и адаптеров (стаб-серверы) — отдельные прогоны.
3. Подъём стека с профилями `embeddings`+`reranker`; health :8004 / :8006 —
   `status: ok`; запросы embed/rerank отвечают 200.
4. Переключение `EMBEDDER=bge_m3_service` + топология embeddings=bge_m3_service:
   ingest → query дают векторы размерности 1024 (запрос через тот же bge).
5. Live-приёмка «гибко»: реальный bge-m3 при доступности GPU/модели; иначе —
   mock-режим контура + шаг реального прогона в runbook.