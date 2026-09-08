# Задачи: M1 (add-prototype-m1-ingestion)

> Toolchain — dev-контейнер `ohw-python:3.13` (Docker) или ВМ.
> `[x]` — только после зелёной проверки (`uv run pytest/ruff/mypy`).

## 1. Контракт (ADR-021 + дока)

- [x] 1.1. `docs/05_adr_log.md`: ADR-021 «DocumentReader и канонический формат документа»
      (блоки text|code|image, source-agnostic content_hash, инвариант).
- [x] 1.2. `docs/02_pipeline_and_normalizer.md` §1: выходной формат INGEST (канон. документ).
- [x] 1.3. `docs/api_reference.md` §5: что INGEST отдаёт пайплайну.
- [x] 1.4. `openspec/.../m1-ingestion`: proposal/design/tasks/spec (этот бандл).

## 2. Модель документа и reader'ы

- [x] 2.1. Модель `Document`/`Block` (text|code|image) + сериализация для `content_hash`
      (нормализация whitespace/переносов).
- [x] 2.2. Интерфейс `DocumentReader.read()` + `TxtReader`, `MdReader`.
- [x] 2.3. `PdfReader` (pypdf): текст + склейка переносов, детект code-блоков,
      извлечение изображений → `type:image` (без OCR).
- [x] 2.4. Реестр читателей по `doc_type` (txt/md/pdf).

## 3. Ingestion API + executor

- [x] 3.1. FastAPI `ingestion_service` (:8002), script `graphrag-ingestion`.
- [x] 3.2. `POST /documents` → `202 {job_id, status, created_at}`; приём файла/URL.
- [x] 3.3. `GET /jobs` (page/page_size), `GET /jobs/{job_id}`, `DELETE /jobs/{job_id}`.
- [x] 3.4. In-process executor: фон. поток, lifecycle, журнал этапов (SQLite).

## 4. Оркестратор и этапы

- [x] 4.1. `orchestrator.py`: прогон 9 этапов, остановка на ошибке, статус `failed`.
- [x] 4.2. Интерфейс `Stage.run()`; `IngestStage` (вызов reader).
- [x] 4.3. `EmbedStage`/`ExtractStage` — заглушки (детерминированные, без GPU).
- [x] 4.4. `ChunkStage` (512/64, code-блоки не рвутся), `NormalizeStage` (Glossary :8003).
- [x] 4.5. `DedupStage` (0.92/0.75/0.85, детерминированно), `ContractStage`.
- [x] 4.6. `ValidateStage` (по `domain_profile.{domain}.yaml`), `CommitStage` (Neo4j атомарно).
      > в M1 COMMIT — через DocumentRegistry (заглушка); реальная Neo4j-атомарность — M2+.

## 5. Document Registry (ADR-014)

- [x] 5.1. Таблицы: `documents`, `job_stages` (SQLite) + JobStore.
- [x] 5.2. Идемпотентность: `(domain, source_url, content_hash)` → no-op (L2-06).
- [x] 5.3. Версии: изменение → `version+1`, старая `superseded`.
- [x] 5.4. Soft-delete: `soft_delete()` → `deleted`.
      > снятие чанков CONTAINS в Neo4j — с реальным COMMIT (M2+).

## 6. Compose и данные

- [x] 6.1. `ingestion-api` (:8002, профиль `ingestion`) + healthcheck.
- [x] 6.2. `embeddings-service` (:8004, профиль `embeddings`, каркас) + healthcheck.
- [x] 6.3. Тестовые sample: `infra/samples/{it|library}/` RU+EN (txt/md/pdf), PDF с кириллицей.
- [x] 6.4. Расширенная glossary-валидация в `config-validate` (секция `glossary`) — M1-запрос из M0.

## 7. Верификация

- [x] 7.1. `uv run pytest -q`, `uv run ruff check`, `uv run mypy` — зелёные (55 passed).
- [x] 7.2. Smoke: сквозная джоба txt/pdf (RU+EN) → COMMIT; повторная → no-op;
      изменение → новая версия (тесты `test_ingestion_api.py`).
- [x] 7.3. Критерии приёмки `proposal.md` (M1) выполнены.
- [x] 7.4. `/review`, коммит + push `origin master`.

## Открытые вопросы (вне M1)

- OQ1. OCR/распознавание изображений — M3 (вместе с боевыми EMBED/EXTRACT).
- OQ2. Боевые bge-m3/Qwen — M3; Task Queue — M2; `.json`-ридер — out-of-scope.
