# Delta Spec: add-real-embeddings-reranker

## Embeddings Service (:8004, профиль `embeddings`, GPU)

- `GET /health` → `{"status": "ok", "service": "embeddings", "model": <str>,
  "dimensions": <int>, "mode": "mock" | "sentence-transformer"}`.
- `POST /api/v1/embed`:
  - request: `{"text": str(≥1 символ), "domain": str?}`;
  - response 200: `{"vector": [float; len == dimensions], "dimensions": <int>}`;
  - 422: текста нет / пуст / не строка.
- `POST /api/v1/embed/batch`:
  - request: `{"texts": [str…, ≥1, каждый не пуст], "domain": str?}`;
  - response 200: `{"vectors": [[float…]], "dimensions": <int>}` (порядок и длина
    совпадают с `texts`);
  - 422 при пустом массиве/пустых элементах.
- Real-режим (`EMBEDDINGS_MOCK` ≠ `true`): lazy-load `SentenceTransformer`
  ("BAAI/bge-m3"), device `EMBEDDINGS_DEVICE` (default `cuda` при доступности, иначе
  `cpu`), текст усекается до `EMBEDDING_MAX_TOKENS` (default 8192), вектор
  L2-нормализован, размерность `EMBEDDING_DIMENSIONS` (default 1024). Ошибка
  загрузки/инференса → 503 с описанием (без тихой деградации).
- Mock-режим (`EMBEDDINGS_MOCK=true`): детерминированный (хэш + нормализация)
  вектор размерности `EMBEDDING_DIMENSIONS`; без ML-зависимостей.
- `create_app(provider=None)` — тесты инжектируют провайдер.

## Reranker Service (:8006, профиль `reranker`, CPU)

- `GET /health` → `{"status": "ok", "service": "reranker", "model": <str>,
  "mode": "mock" | "cross-encoder"}`.
- `POST /api/v1/rerank`:
  - request: `{"query": str(≥1), "chunks": [{"id": str?, "text": str}…, ≥1]}`;
  - response 200: `{"scores": [float; len == len(chunks)]}` — индекс соответствует
    входному порядку чанков;
  - 422: пустой query / пустые chunks / отсутствует `text`.
- Real: `sentence_transformers.CrossEncoder("BAAI/bge-reranker-base")` (CPU);
  ошибка загрузки/инференса → 503.
- Mock (`RERANKER_MOCK=true`): скор = взвешенное пересечение лексических токенов
  (0..1), детерминированно; порядок сохраняется.
- `create_app(scorer=None)` — тесты инжектируют скорер.

## Адаптеры `retrieval/adapters/bge.py`

- `BgeM3ServiceAdapter(Embedder)`: `embed(text, domain="")` → `list[float]`
  (POST `/api/v1/embed`); HTTP-ошибка/неверная размерность/мальформированный ответ →
  `RuntimeError` (fail-fast).
- `BgeRerankerAdapter(Reranker)`: `rerank(query, chunks)` → `list[float]`,
  `len == len(chunks)`; ошибка → `RuntimeError`.
- Соединение: base_url/token?/timeout из аргументов конструктора; `from_env()`-фабрики
  возвращают инстансы с env-параметрами (`EMBEDDINGS_URL`, `RERANKER_URL`,
  `*_TIMEOUT_S`). Auth `X-API-Key` берётся из `AUTH_API_KEY`/`GRAPH_AUTH_API_KEY`
  (как `TopologyClient.from_env`).

## Каталог и сборка (`retrieval/adapters/factory.py`)

- `ADAPTER_CATALOG["embeddings"] == ["deterministic", "bge_m3_service"]`;
  `ADAPTER_CATALOG["reranker"] == ["noop", "bge_reranker"]`.
- `build_embedder()`/`build_reranker()`/`build_adapters(map)` собирают bge-провайдеры
  только при их выборе (без ML-импортов на import-time).
- Приоритет слота не меняется: карта топологии > env > дефолт.
- Topology `GET /api/v1/config/adapters/available` отражает новый каталог.

## Ingestion (EmbedStage, L2-04)

- `EmbedStage(embedder=None)`; `None` → `DeterministicEmbedder(dim=8)` (поведение M2
  и старые тесты неизменны). `run()`: `meta["embedding"] = embedder.embed(chunk, domain)`.
- `build_analyzer(..., embedder=None)`/`Executor(..., embedder=None)`; app.py:
  `embedder = build_embedder()` (env `EMBEDDER`). Query-сторона строит эмбеддер из
  той же фабрики — ось консистентна.

## Env-параметры (новые)

| var | default | назначение |
|---|---|---|
| `EMBEDDINGS_URL` | `http://embeddings-service:8004` | клиент bge_m3_service |
| `RERANKER_URL` | `http://reranker:8006` | клиент bge_reranker |
| `EMBEDDINGS_TIMEOUT_S` | `5.0` | HTTP-таймаут клиента |
| `RERANKER_TIMEOUT_S` | `5.0` | HTTP-таймаут клиента |
| `EMBEDDINGS_MOCK` | `false` | mock-режим сервиса :8004 |
| `RERANKER_MOCK` | `false` | mock-режим сервиса :8006 |
| `EMBEDDINGS_DEVICE` | `cuda`→fallback `cpu` | устройство bge-m3 |
| `EMBEDDING_DIMENSIONS` | `1024` | размерность оси (сервис) |
| `EMBEDDING_MAX_TOKENS` | `8192` | усечение текста (сервис) |

## Композ/Docker

- `Dockerfile.embeddings`/`Dockerfile.reranker` — базовый слой (как Dockerfile) +
  `uv sync --no-dev --frozen` + `uv pip install` ML-пакетов в `.venv`; ENTRYPOINT
  `uv run --no-sync`; CMD `graphrag-embeddings` / `graphrag-reranker`.
- `pyproject.toml` `[project.scripts]`: `graphrag-embeddings`, `graphrag-reranker`.
- `compose.yaml`: `embeddings-service` — `build`, `command`, env (модель/размерность/
  device/mock); `reranker` — `build`, `command`, порт `8006:8006`, healthcheck.
- `infra_topology.yaml`: providers остаются `embeddings: deterministic`,
  `reranker: noop` (запуск без GPU); комментарий о реализованных bge-провайдерах;
  переключение на bge — шаг runbook.

## Не входит

- Семантический кэш (бандл 3), hot-reload Ingestion (env-only, M2), batch-эмбеддинг
  в EmbedStage (эндпоинт `/embed/batch` предоставлен; пайплайн — последовательный
  `embed`), коннекторы источников (M6).