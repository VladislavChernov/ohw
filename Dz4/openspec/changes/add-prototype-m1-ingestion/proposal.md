# Proposal: Веха 1 — Ingestion Pipeline (9 этапов) + контракт DocumentReader

> Продолжение ДЗ4 после M0 (`add-prototype-m0-services`, коммит `9225b3a`).
> Строится на M0: Config/Glossary (:8001/:8003), Neo4j — уже в контуре.
> Всё автономно — общий `D:\Otus\infra` / shared ollama / `ohw_kit` не используются.

## Зачем

M0 дал контур-основу (Config, Glossary, Neo4j), но **нет исполнимого пайплайна загрузки**:
неоткуда брать данные в граф. Чтобы M2 (Query) было что искать, нужен сквозной
Ingestion Pipeline, который доводит документ от файла до атомарной записи в Neo4j.
Это «веха 1» по `docs/prototype_requirements.md §6` и обязательный этап работы
(одна из причин: идемпотентность INGEST — инвариант L2-06, ADR-014).

Дополнительно вскрылся пробел контрактов: **не зафиксирован формат, который INGEST
обязан отдать пайплайну после чтения источника.** Без этого загрузчик неявно связан
с типом источника (`.txt/.md/.pdf/...`), а подключение OCR/распознавания картинок
стало бы правкой 8 этапов, а не одного ридера. Нужно зафиксировать **канонический
формат документа** и интерфейс **DocumentReader** — как отдельный ADR (см. ниже).

## BR (бизнес-требования)

- **BR-1. Сквозной пайплайн.** Один вызов Ingestion API проходит все 9 этапов
  (INGEST→CHUNK→EMBED→EXTRACT→NORMALIZE→DEDUP→CONTRACT→VALIDATE→COMMIT) до записи
  в Neo4j без ручного вмешательства (критерий готовности §8.1).
- **BR-2. Контракт Ingestion API.** `docs/api_reference.md §5` / ADR-018:
  `POST /documents`, `GET /jobs`, `GET/DELETE /jobs/{id}`, lifecycle джобы.
- **BR-3. Идемпотентность и версии.** Повторная загрузка неизменённого `source_url`
  — no-op (L2-06); изменение контента — новая версия, старая `superseded` (ADR-014).
- **BR-4. Документ-агностичность.** Пайплайн зависит только от канонического формата
  документа, не от источника; добавление нового ридера/OCR не трогает этапы CHUNK…COMMIT.
- **BR-5. Мультиязычность.** Провалидировать пайплайн на RU и EN тексте, включая
  русскую кириллицу в PDF.

## Что делаем

- **Контракт:** новый `docs/05_adr_log.md` ADR-021 «DocumentReader и канонический
  формат документа» + секция в `docs/02_pipeline_and_normalizer.md §1` и
  `api_reference.md §5` (что INGEST отдаёт пайплайну).
- **DocumentReader (интерфейс):** `ingestion/readers/base.py` — единственный контракт
  «источник → канонический документ». Реализации: `txt`, `md`, `pdf` (pypdf).
- **Канонический формат:** `Document { source_id, doc_type, domain, content_hash,
  blocks: [{type: text|code|image, page, order, data, ...}] }`; `content_hash`
  считается с канонического вида (после нормализации переносов), источник-агностичен.
- **Ingestion API (:8002) + in-process executor:** джоба в фоновом потоке, журнал
  этапов в SQLite, lifecycle по ADR-018.
- **Оркестратор 9 этапов + адаптеры-интерфейсы:** EMBED/EXTRACT — заглушки
  (детерминированные фейки), боевые bge-m3/Qwen — в M3.
- **Document Registry (SQLite):** content-hash идемпотентность, версии, soft-delete
  (ADR-014).
- **Этапы:** CHUNK (512/64), NORMALIZE (Glossary :8003), DEDUP (0.92/0.75/0.85),
  CONTRACT, VALIDATE, COMMIT (атомарно в Neo4j, L2-04).
- **Compose:** `ingestion-api` (:8002), `embeddings-service` (:8004, каркас),
  healthcheck'и; расширенная glossary-валидация в `config-validate` (запрос из M0).
- **Данные для теста:** IT — два «алгоритмических» текста (RU+EN); библиотека —
  «Война и мир» (RU) + классический англоязычный текст (EN). PDF для RU-кириллицы.

## Не делаем в этой вехе

- OCR / распознавание изображений — **M3** (вместе с боевыми EMBED/EXTRACT);
  в M1 картинки извлекаются как блоки `type:image` (без смысла) — не теряются структурно.
- Реальные bge-m3 / Qwen — M3 (в M1 заглушки).
- Task Queue (Valkey/Redis Streams) — M2 (в M1 in-process executor).
- `.json`-ридер — out-of-scope (нет структурированных источников; при необходимости — отдельный шаг).
- Query/Retriever/адаптеры-переключатели — M2/M3.

## Проверка (критерии приёмки M1)

- Сквозной вызов `POST /api/v1/ingestion/documents` (txt/md/pdf, RU и EN домены)
  завершается `COMMIT`; `GET /jobs/{id}` показывает `succeeded` и журнал 9 этапов.
- Повторный INGEST того же `source_url` (тот же content-hash) — no-op, без новой версии.
- Изменение контента — создаёт `version+1`, старая версия `superseded` (ADR-014).
- PDF с кириллицей извлекается корректно (нормализация переносов перед хэшем).
- Код-блоки и изображения в PDF не теряются: представлены блоками `type:code`/`type:image`.
- Контракт проверен: этапы CHUNK…COMMIT не знают о `.txt/.md/.pdf` (работают только
  с каноническим `Document`).
- В рабочей среде (dev-container/ВМ): `uv run pytest -q`, `uv run ruff check`,
  `uv run mypy` — зелёные.
