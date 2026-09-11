# Задачи

## 1. Embeddings Service (:8004)

- [x] 1.1. `embeddings_service/model.py`: провайдер эмбеддинга с инъекцией для тестов;
      real-режим — lazy `SentenceTransformer("BAAI/bge-m3")` (`EMBEDDINGS_DEVICE`, L2-нормализация,
      размерность `EMBEDDING_MODEL_DIMENSIONS`), mock-режим `EMBEDDINGS_MOCK=true` —
      детерминированный нормализованный вектор нужной размерности.
- [x] 1.2. `embeddings_service/app.py` (FastAPI :8004): `GET /health`,
      `POST /api/v1/embed` (`{text, domain?}` → `{vector, dimensions}`),
      `POST /api/v1/embed/batch` (`{texts[], domain?}` → `{vectors[], dimensions}`);
      пустой/пустые тексты → 422; `create_app(provider=None)` для тестов.
- [x] 1.3. `pyproject.toml` script `graphrag-embeddings` (uvicorn :8004).

## 2. Reranker Service (:8006)

- [x] 2.1. `reranker_service/model.py`: lazy `CrossEncoder("BAAI/bge-reranker-base")`
      (CPU); `RERANKER_MOCK=true` — скор по пересечению токенов; инъекция для тестов.
- [x] 2.2. `reranker_service/app.py` (FastAPI :8006): `GET /health`,
      `POST /api/v1/rerank` (`{query, chunks[]}` → `{scores[]}`, выравнивание по
      индексу входных чанков); пустой query/chunks → 422; `create_app(scorer=None)`.
- [x] 2.3. `pyproject.toml` script `graphrag-reranker` (uvicorn :8006).

## 3. Адаптеры-клиенты (retrieval/adapters/bge.py) + каталог

- [x] 3.1. `retrieval/adapters/bge.py`: `BgeM3ServiceAdapter(Embedder).embed` —
      POST `/api/v1/embed`; `BgeRerankerAdapter(Reranker).rerank` — POST
      `/api/v1/rerank`; fail-fast на ошибки HTTP/размерности (не тихий фоллбэк).
- [x] 3.2. `retrieval/adapters/factory.py`: `ADAPTER_CATALOG` `embeddings` +=
      `bge_m3_service`, `reranker` += `bge_reranker`; сборка по env
      `EMBEDDINGS_URL` (default `http://embeddings-service:8004`), `RERANKER_URL`
      (default `http://reranker:8006`), `*_TIMEOUT_S`; `build_embedder()/build_reranker()`.

## 4. Ingestion: EmbedStage → адаптер

- [x] 4.1. `orchestrator.py::EmbedStage(embedder=None)`: по умолчанию
      `DeterministicEmbedder(dim=8)` (M2-совместимость); `run()` пишет
      `meta["embedding"] = embedder.embed(chunk, ctx.domain)` (L2-04).
- [x] 4.2. `ingestion_service/app.py`: `build_analyzer(..., embedder=None)` и
      `Executor(..., embedder=None)`; источник — `build_embedder()` (env `EMBEDDER`,
      фоллбэк deterministic).

## 5. Образы и compose

- [x] 5.1. `Dockerfile.embeddings`: базовый слой + `uv sync` (как Dockerfile) +
      pip-установка torch/sentence-transformers в `.venv` сервисного образа; ENTRYPOINT
      `uv run --no-sync`, CMD `graphrag-embeddings`.
- [x] 5.2. `Dockerfile.reranker`: то же + sentence-transformers; CMD `graphrag-reranker`.
- [x] 5.3. `infra/compose.yaml`: `embeddings-service` — `build`/`command`/env;
      `reranker` — `build`/`command`/`ports 8006:8006`/healthcheck;
      `infra_topology.yaml` — комментарий о реализации bge-провайдеров (база остаётся
      `deterministic`/`noop`).

## 6. Тесты

- [x] 6.1. `tests/test_embeddings_service.py`: health, embed, batch, 422,
      валидация размерности через инжектированный провайдер.
- [x] 6.2. `tests/test_reranker_service.py`: health, rerank, выравнивание скоров, 422.
- [x] 6.3. `tests/test_bge_adapters.py`: стаб-HTTP-серверы (паттерн
      `test_domain_activation.py`) — скоры/векторы, ошибка сервиса → fail-fast.
- [x] 6.4. `test_factory_adapters.py`: каталог + `build_adapters` для
      `bge_m3_service`/`bge_reranker` (env-URL); `test_topology_service.py` —
      актуализация `available` (embeddings + reranker).
- [x] 6.5. Интеграция ingest: `EmbedStage` с инжектированным эмбеддером пишет его
      вектор (L2-04); existing-тесты (EMBED → deterministic) не сломаны.
- [x] 6.6. `uv run pytest -q` зелёно (полный прогон); `uv run ruff check .`,
      `uv run mypy src` чисто.

## 7. Документация и приёмка

- [x] 7.1. `docs/demo_runbook.md`: раздел «Реальные эмбеддинги и реранкер (M3.2)» —
      профили, переключение топологии/EMBEDDER, mock vs real, L4-01 фазирование.
- [x] 7.2. `docs/04` §1: строка порта :8006 (Reranker Service); `prototype_requirements.md`
      — статус M3-подзадачи «реальные эмбеддинги» после live-приёмки; `history.md`.
- [x] 7.3. Live-приёмка «гибко»: mock-контур :8004/:8006 (health, embed 1024, batch,
      rerank-упорядоченность); реальный инференс (torch+sentence-transformers на CPU:
      MiniLM encode 384-dim, cross-encoder rerank top-1) — bge-m3-веса подключаются
      env-моделью (шаг реального прогона зафиксирован в runbook, веса ~2.3 ГБ).
