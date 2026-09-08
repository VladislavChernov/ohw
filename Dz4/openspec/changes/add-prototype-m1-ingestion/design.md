# Design: add-prototype-m1-ingestion

## Решения

### D1. Канонический формат документа (контракт, ADR-021)

INGEST обязан отдать пайплайну **канонический документ**, полностью источник-агностичный:

```json
{
  "source_id": "it://books/algorithms-ru",
  "source_url": "https://example/algorithms.pdf",
  "domain": "it",
  "doc_type": "pdf",
  "content_hash": "sha256:...",
  "blocks": [
    {"type": "text",  "page": 3,  "order": 0, "data": "..."},
    {"type": "code",  "page": 4,  "order": 1, "data": "def quicksort(...)"},
    {"type": "image", "page": 5,  "order": 2, "data": {"ref": "img1", "w": 800, "h": 600}}
  ]
}
```

- `type`: `text` | `code` | `image`.
- `content_hash` считается по сериализации **нормализованного** канонического вида
  (переносы склеены, whitespace стабилен) — с источника не снимается. Это делает
  идемпотентность (L2-06/ADR-014) источник-независимой и устойчивой к ошибкам
  извлечения.
- **Инвариант (новый, L-запись):** этапы CHUNK…COMMIT читают ТОЛЬКО `Document`
  (канонический), никогда — исходный файл/байты.

### D2. DocumentReader — интерфейс и реализации

- `src/graphrag_proto/ingestion/readers/base.py`: `DocumentReader.read(source) -> Document`.
- Реализации по `doc_type`: `TxtReader`, `MdReader`, `PdfReader`.
- `PdfReader` (pypdf):
  - извлекает текст страниц (склейка переносов по `-\n`);
  - детектит кодовые блоки (моноширинный шрифт / маркеры листинга) → `type:code`;
  - извлекает изображения (`pypdf`/`pymupdf`-опционально) → `type:image` c `ref`
    (сырые байты/указатель на стор), **без OCR** (OCR — M3).
- Замена «тулзы распознавания» (OCR и т.п.) = новая реализация `DocumentReader`,
  этапы не меняются.

### D3. Ingestion API + in-process executor

- FastAPI-приложение `graphrag_proto.ingestion_service.app` (:8002), entry `graphrag-ingestion`.
- Эндпоинты точно по ADR-018 `api_reference.md §5`:
  `POST /api/v1/ingestion/documents`, `GET /api/v1/ingestion/jobs?page&page_size`,
  `GET /api/v1/ingestion/jobs/{job_id}`, `DELETE /api/v1/ingestion/jobs/{job_id}`.
- `POST /documents` → `202 {job_id, status, created_at}`, джоба исполняется в
  фоновом потоке (in-process executor). Миграция на Task Queue — M2.
- Lifecycle: `queued → running (stage) → succeeded | failed | cancelled`.
- Журнал этапов — SQLite (таблица job_stages).

### D4. Оркестратор этапов + адаптеры

- `pipeline/orchestrator.py`: последовательный прогон 9 этапов с остановкой на
  ошибке и справедливым статусом `failed` + сообщением.
- Каждый этап — класс-адаптер с единым интерфейсом `Stage.run(ctx, document) -> ctx`.
- `EmbedStage`, `ExtractStage` — **заглушки**: детерминированная функция (напр.
  стабильный хэш-вектор фикс. размерности), эмбеддинги/сущности выглядят правильно,
  но не требуют GPU/моделей. Боевые — M3.

### D5. Document Registry и этапы

- `registry.py` (SQLite): `documents(doc_id, source_url, domain, doc_type, version,
  content_hash, status)`; идемпотентность по `(domain, source_url, content_hash)`:
  совпадение → no-op; изменение → `version+1`, старая `superseded`; soft-delete —
  `status: deleted` снятие чанков (каскад через `CONTAINS`).
- Этапы по `docs/02 §1` + `docs/invariants.md`:
  - **CHUNK**: sliding_window 512/64 (по блокам; code-блоки не рвутся),
  - **NORMALIZE**: Glossary :8003 `resolve` (RU+EN), unicode/лог нормализация,
  - **DEDUP**: пороги 0.92/0.75/0.85 (детерминированно на канонич. тексте),
  - **CONTRACT**, **VALIDATE** (по `domain_profile.{domain}.yaml`),
  - **COMMIT**: атомарная запись в Neo4j (транзакция, rollback при ошибке — L2-04),
    затем обновление Document Registry.

### D6. Compose и данные

- `ingestion-api` (:8002, профиль `ingestion`, depends_on config/glossary/neo4j) + healthcheck.
- `embeddings-service` (:8004, профиль `embeddings`, каркас, GPU) — healthcheck.
- График L4-01: профили `embeddings`/`ingestion` не конфликтуют по VRAM (в M1
  embeddings — каркас без боевых моделей).
- Тестовые данные: `infra/samples/{domain}/` — RU/EN тексты; PDF с кириллицей
  («Война и мир» — класс.:RU, англ. классика — EN).
- Расширенная glossary-валидация в `config-validate` (секция `glossary`) — закрывает
  M1-запрос из M0.

## Модули

| Модуль | Изменение |
|---|---|
| `docs/05_adr_log.md` | + ADR-021 (DocumentReader / канонич. формат) |
| `docs/02_pipeline_and_normalizer.md` §1 | + секция «выходной формат INGEST» |
| `docs/api_reference.md` §5 | + что INGEST отдаёт пайплайну |
| `openspec/changes/add-prototype-m1-ingestion/*` | proposal/design/tasks/spec (этот бандл) |
| `prototype/src/graphrag_proto/ingestion_service/` | app + pipeline + readers + registry + stores |
| `prototype/pyproject.toml` | + `pypdf`(+stub), script `graphrag-ingestion` |
| `prototype/tests/` | test_ingestion (API, пайплайн, registry, readers) |
| `prototype/infra/compose.yaml` | ingestion-api, embeddings-service, healthcheck'и |
| `prototype/infra/samples/` | RU/EN тестовые тексты + PDF |

## Open questions

- OQ1. `image`-блоки храним как BLOB в SQLite и/или файлами на диске; ссылка `ref`
  достаточна для индексации без OCR. Решить на имплементации (по умолчанию —
  файлы в `runtime/images/`, в БД указатель).
- OQ2. KV/граф store для M1: записи в Neo4j идемпотентно (MERGE по canonical_name +
  source_ids), векторный индекс Neo4j — только M3 (в M1 чанк-эмбеддинги заглушечные,
  вектор не индексируется всерьёз).
