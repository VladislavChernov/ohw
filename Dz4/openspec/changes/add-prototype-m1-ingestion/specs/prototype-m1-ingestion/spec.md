# Delta Spec: add-prototype-m1-ingestion (Ingestion Pipeline + DocumentReader)

## ADDED Requirements

### Requirement: Канонический формат документа (ADR-021)

INGEST ДОЛЖЕН преобразовывать любой источник в **канонический документ**,
источник-агностичный, с блоками `text|code|image` и источник-независимым `content_hash`.
Этапы CHUNK…COMMIT ДОЛЖНЫ читать только этот канонический документ и НЕ должны
зависеть от типа источника (`.txt/.md/.pdf`).

#### Scenario: Канонический вид не зависит от источника

- **WHEN** один и тот же текст подан как `.txt` и как `.md`
- **THEN** (после нормализации) их `content_hash` совпадает, а этапы работают одинаково

### Requirement: DocumentReader — интерфейс и реализации

Проект ДОЛЖЕН иметь единый интерфейс `DocumentReader.read(source) -> Document` и
реализации для `txt`, `md`, `pdf`. Добавление нового ридера (в т.ч. OCR-расширения)
НЕ ДОЛЖНО менять этапы CHUNK…COMMIT.

#### Scenario: PDF-ридер извлекает смешанный контент

- **WHEN** обработан PDF с текстом, листингом и картинкой
- **THEN** в `Document.blocks` присутствуют блоки `type:text`, `type:code`, `type:image`
  (картинка не теряется структурно, без OCR)

### Requirement: Ingestion API (:8002) и lifecycle джобы

Ingestion API ДОЛЖЕН реализовать контракт ADR-018: `POST /documents → 202 {job_id,
status, created_at}`, `GET /jobs` (пагинация), `GET/DELETE /jobs/{job_id}`, lifecycle
`queued → running (INGEST|…|COMMIT) → succeeded | failed | cancelled`. Джоба ДОЛЖНА
исполняться асинхронно (in-process executor в M1).

#### Scenario: Сквозная джоба до COMMIT

- **WHEN** вызван `POST /documents` для txt/md/pdf (RU и EN домены)
- **THEN** `GET /jobs/{job_id}` в итоге возвращает `status: succeeded` и журнал из 9 этапов

#### Scenario: Отмена джобы

- **WHEN** вызван `DELETE /jobs/{job_id}` для `queued/running`-джобы
- **THEN** джоба получает `status: cancelled`

### Requirement: Идемпотентность INGEST и версии (ADR-014)

Повторная загрузка неизменённого `source_url` (тот же `content_hash`) ДОЛЖНА быть
no-op (L2-06). Изменение контента ДОЛЖНО создавать новую версию, старую — помечать
`superseded`. Удаление — soft delete (`deleted`).

#### Scenario: Повторная загрузка — no-op

- **WHEN** `POST /documents` повторно для того же `source_url` без изменения контента
- **THEN** не создаётся новая версия, джоба завершается без дубликатов

#### Scenario: Изменение контента — новая версия

- **WHEN** `POST /documents` для `source_url`, контент которого изменился
- **THEN** создаётся `version+1`, предыдущая версия получает `status: superseded`

### Requirement: Этапы CHUNK/NORMALIZE/DEDUP/CONTRACT/VALIDATE/COMMIT

Пайплайн ДОЛЖЕН исполнять этапы в порядке `docs/02 §1` со значениями: CHUNK 512/64,
NORMALIZE через Glossary :8003 (RU+EN), DEDUP 0.92/0.75/0.85, COMMIT атомарно в Neo4j
(rollback при ошибке, L2-04) с последующим обновлением Document Registry.

#### Scenario: Атомарность COMMIT

- **WHEN** на этапе COMMIT происходит ошибка записи в Neo4j
- **THEN** транзакция откатывается, граф не содержит частичных записей, джоба — `failed`

### Requirement: DOMAIN-AGNOSTICITY и мультиязычность

Пайплайн ДОЛЖЕН обрабатывать домены `it` и `library`, текст RU и EN, включая
кириллицу из PDF (нормализация переносов перед `content_hash`).

#### Scenario: Кириллица из PDF

- **WHEN** загружен PDF с русским текстом и переносами
- **THEN** текст извлекается корректно, переносы склеены, `content_hash` стабилен
