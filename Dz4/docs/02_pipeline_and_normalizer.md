# Документация: Ingestion Pipeline и Normalizer v3

> **Статус:** vector-only baseline + optional offline graph experiment

## 1. Primitive pipeline

Фактический порядок стадий (`ingestion_service/app.py`, сборка `Analyzer`):

| # | Стадия | Что делает | Зависимость от профиля |
|---|---|---|---|
1 | `IngestStage` | reader создаёт canonical `Document` из txt/md/pdf | нет |
2 | `ChunkStage` | текст режется выбранным `Chunker` | optional hints; недоступный профиль не блокирует дефолтный chunker |
3 | `EmbedStage` | выбранный Embedder вычисляет vectors | нет |
4 | `ExtractStage` | извлечение сущностей и связей; при `llm_enabled` используется промпт профиля | промпт, `llm_enabled`; `optional_failure=True` |
5 | `NormalizeStage` | разрешение вариантов и aliases | glossary URL |
6 | `DedupStage` | слияние сущностей по точному совпадению нормализованного ключа | нет |
7 | `ContractStage` | приведение сущностей к контракту записи | нет |
8 | `ValidateStage` | проверка результата перед записью | нет |
9 | `CommitStage` | запись в vector и graph storage, обновление registry и projection state | `graph_optional=True` |

Профиль, AI и tags не обязательны: `CommitStage` работает без graph store, а `ExtractStage`
обозначен как optional. Ошибка optional AI enrichment сохраняется как degraded/quarantine status
и не превращает primitive ingest в failed job, если не запрошен strict enrichment mode. Offline
job также не изменяет активный vector baseline: он строит экспериментальную projection и может
backfill-ить `context_ids`/`tag_ids` в metadata.

### 1.1 Как выбирается стратегия записи

Векторная и графовая записи — **не два последовательных шага**, а один `CommitStage` с
внутренним выбором стратегии:

```text
graph store отсутствует            -> _write_vector_only
vector store отсутствует           -> RuntimeError (vector baseline обязателен)
оба есть, оба atomic и engine_key
  совпадает                         -> атомарная пара
иначе                              -> best_effort
```

Порядок внутри `_run_locked` (`orchestrator.py`): поднять `source_lock`, проверить идемпотентность
(такой же `content_hash` и нет tags/links/metadata — запись пропускается), посчитать
`data_revision` и `projection_revision`, выполнить запись, затем обновить registry и projection
state. Если запись упала с `compensated=True`, делается `soft_delete`; если упала любая другая
ошибка при `graph_optional=True`, статус становится `degraded` и выполняется **повторная запись
только вектора** — то есть vector baseline всё равно фиксируется.

## 2. Два lifecycle graph experiment

### 2.1 Inline enrichment

Inline enrichment выполняется best-effort после vector commit. Он может записать generic nodes,
edges и metadata за один ingest job, но его ошибка не отменяет документ и не блокирует baseline.

### 2.2 Offline enrichment/rebuild

Offline enrichment — отдельный идемпотентный replay корпуса. Он строит или обновляет graph
projection, backfill-ит metadata и фиксирует projection revision/readiness. Job key состоит из
`domain`, `data_revision`, `projection_revision` и `config_fingerprint`; claim/lease не позволяет
двум writer обрабатывать один projection одновременно. Graph batch и metadata backfill могут
повторяться без дублей и без изменения content, embedding или document registry.

State store допускает статусы `pending`, `ready`, `degraded`, `stale` и `failed`. Только
`ready` с актуальной `data_revision` разрешает graph experiment. Пока job не завершён, индекс
отстал или state отсутствует, query получает vector-only fallback с degraded marker; job и
baseline не блокируют друг друга.

## 3. Identity и multilingual aliases

Документ идентифицируется по `(domain, source_url, content_hash)`. Контекстный тег имеет
стабильный `tag_id` внутри domain, `canonical_name` и aliases. Glossary resolution может связать
английское и русское названия одного алгоритма; неоднозначные кандидаты остаются отдельными до
явного user merge. Если ручной тег не имеет `tag_id`, но совпадает ровно с одним известным
context node, он переиспользует существующий идентификатор; при нескольких кандидатах
идентификатор не выбирается молча.

**AI-дедупликации в прототипе нет.** `NormalizeStage` выполняет только resolve через Glossary
HTTP. Ни LLM-верификации пар, ни multilingual embeddings, ни порогов сходства в рантайме не
существует; косинусная политика вынесена в план `docs/plans/cosine-dedup.md` и инвариант
`L3-02a`.

## 4. Graph write

Разрешены generic node/edge properties и kinds. Проверяются только технические условия:
корректный endpoint, domain, provenance и отсутствие опасных операций. `_validate_ontology`,
profile edge whitelist, `ensure_schema` и Neo4j constraints не являются ingest-гейтом. Фактический
состав рёбер и набор видов — `data_model.md` §3.

### 4.1 Правило слияния при записи ноды (2026-09-27)

Одна нода может присутствовать в метаданных **разных документов** — это штатный случай, а не
конфликт: две методички упоминают «индексирование», и обе дают один `node_id`. Слияние
происходит в момент записи (`_upsert_nodes`, `retrieval/adapters/neo4j.py`), а **не** в
пайплайне: `DedupStage` (стадия 6) видит только записи одного документа и сливает лишь то,
что модель извлекла из разных его чанков.

Правила по группам полей — они разные, и это главное, что нужно знать:

| группа полей | правило | чем определяется |
|---|---|---|
| provenance: `sources`, `source_ids`, `chunk_ids` | **объединение** с уже имеющимися, без замены | не зависит ни от чего |
| `origin = 'user'` (ручной тег) | **не перезаписывается** не-`user` записью | `preserve_manual` + `CASE WHEN` в запросе |
| вложенный `properties` | при `origin = 'user'` сохраняется ручное | `preserve_manual` |
| остальные скалярные: `name`, `canonical_name`, `description`, `confidence`, `category`, `id` | **последний записавший** | порядок обработки корпуса |

**Последняя строка — сознательное следствие, а не недоработка, и она обязана быть названа.**
Пока все пишущие документы имеют `origin = ai`, «последний записавший» означает «тот, чья
джоба обработалась последней», то есть **содержимое ноды зависит от порядка загрузки корпуса**.
Один и тот же корпус, загруженный в разном порядке, даст разные `description` при одинаковом
наборе документов. Практическое следствие: `description` ноды описывает последний
обработанный документ, а не документ вообще.

Повторная загрузка **того же** содержимого это не меняет: `CommitStage.try_noop` смотрит
`(domain, source_url)` и сравнивает `content_hash`, и документ с тем же содержимым (и без
ручных `tags`/`links`/`metadata`) становится no-op, ничего не перезаписывая.

**Новая ревизия того же документа — переписывает ноду.** Хэш отличается, значит это не
no-op, сущности извлекаются заново, и те же `node_id` получают новые скалярные свойства. То
есть ревизия старого документа способна затереть `description`, который дал другой,
более новый документ, — а provenance при этом корректно объединится. Это единственное
место, где «последний записавший» явно расходится с ожиданием «новейшая ревизия важнее», и
оно требует решения владельца, а не молчаливого сохранения.

Отдельно про `DedupStage` (внутри одного документа, стадия 6): там действуют **две
противоположные политики** на одних и тех же записях —

- `canonical`, `canonical_name`, `name`, `origin`, `confidence`: последняя запись при
  `_origin_rank >= current_rank` выигрывает (ранги: `ai` 1, `system` 2, `user` 3);
- `id`, `description`, `category`: заполняются, **только если ещё не заполнены**, то есть
  выигрывает первый;

Поэтому в одном документе имя ноды приходит из последнего извлечения, а `description` — из
первого. Повторная загрузка того же хэша это не исправляет (no-op), поэтому «плохое»
описание, извлечённое первым, живёт на узле, пока содержимое документа не изменится.

Сортировка нод по `node_id` перед записью (ADR-028) — это **защита от deadlock** при
поштучном MERGE, а не правило приоритета: порядок захвата замков должен быть детерминированным,
и он не определяет, чьи значения победят.

**Что из перечисленного закреплено тестом, а что только прочитано в коде.** Это важно
различать: «задокументировано» не значит «проверяется».

| правило | тест |
|---|---|
| `user` не перезаписывается, вложенный `properties` сохраняется | есть: `test_neo4j_custom_properties_preserve_manual_origin` |
| provenance объединяется, а не заменяется | есть: `test_neo4j_upsert_merges_context_provenance` |
| скалярные: последний записавший | **нет теста** — следует из `MERGE ... SET` без условия |
| `DedupStage`: `description` — первый, `name` — последний | **нет теста** |
| `system`-нода не защищена при записи | **нет теста** |

Отсутствие теста здесь — не «всё сломано», а «правило не зафиксировано»: правка кода может
изменить его незаметно, и документация станет ложью молча. Это тот же класс, что
`canonicalization.*.layers` в профиле: объявлено, но не применяется — только здесь наоборот,
применяется, но не объявлено, и цена ошибки выше.

### 4.2 Что здесь не решено

1. Приоритет скалярных полей при равном `origin = ai` (сейчас — последний документ).
2. Что делать с `description`, когда ревизия документа приносит лучшее описание: сейчас
   перезапишет, и provenance это не компенсирует.
3. Расхождение между рангами `_origin_rank` (три уровня при слиянии внутри документа) и
   защитой при записи (только `origin = 'user'`). Нода с `origin = 'system'` внутри своего
   документа не перезаписывается извлечённой записью (ранг 2 > 1), но **при записи она не
   защищена**: `CASE WHEN` срабатывает только на `user`, поэтому `ai`-запись из другого
   документа перезапишет `system`-ноду. Проверяемо и должно быть сведено к одному правилу.

## 5. Retrieval pipeline

```text
baseline: vector search → metadata sources → rerank/context assembly
optional graph experiment: vector seeds → bounded graph expansion → boost/rerank → context assembly
```

Vector-only baseline не вызывает graph. Graph experiment получает seeds из vector metadata,
выполняет ограниченный traversal и сохраняет path/depth/confidence для eval. При недоступном,
неполном или устаревшем graph используется vector-only fallback с degraded marker.
