# Delta Spec: add-source-connectors

> Данная спецификация фиксирует **будущее** поведение. Реализации в этом бандле нет:
> задачи в `tasks.md` остаются `[ ]`, пока не будет открыт бандл реализации.

## ADDED Requirements

### Requirement: Контракт SourceProvider (планируемый)

Коннектор внешнего источника ДОЛЖЕН реализовать интерфейс
`fetch(source: SourceRef, creds) -> bytes`, где
`SourceRef {source_url, domain, doc_type}`. Транспорт, аутентификация и пагинация —
ответственность коннектора; интерпретация байтов — ответственность `DocumentReader`
(ADR-021). Отдельные коннекторы НЕ ДОЛЖНЫ изменять пайплайн INGEST→COMMIT,
`DocumentReader`, Document Registry или `ALLOWED_DOC_TYPES`.

#### Scenario: Подключение коннектора без изменения ядра

- **GIVEN** проект с Ingestion API (:8002, ADR-018) и реестром ридеров `registry.py::factory`
- **WHEN** добавляется коннектор (например, Jira): регистрирует свой `doc_type`
  и материализует данные в `runtime/uploads/{job_id}.{doc_type}`
- **THEN** существующий push-путь `POST /documents` и пайплайн работают без изменений;
  источник попадает в Document Registry с идемпотентным `content_hash` (ADR-014)

#### Scenario: idempotent re-ingest

- **WHEN** тот же источник передаётся повторно с тем же содержимым
- **THEN** `content_hash` совпадает — дубли не создаются, версия источника по ADR-014

### Requirement: Модульность и безопасность (планируемые)

Коннекторы ДОЛЖНЫ размещаться вне ядра (соседний модуль `ingestion_service/connectors/`),
креды — во внешнем сторедже/env (не в коде и не в логах; `docs/security.md` §3).

### Requirement: Не-регрессия (обязательна)

Бандл реализации НЕ ДОЛЖЕН менять: контракт `POST /api/v1/ingestion/documents`,
контракты ABC-адаптеров (graph_store/vector_store/embeddings/reranker/llm), события
ADR-016, SSOT `namespaces.yaml`. `uv run pytest -q`, `ruff`, `mypy` — зелёные.