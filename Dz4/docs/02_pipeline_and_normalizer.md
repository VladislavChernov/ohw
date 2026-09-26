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
стабильный `tag_id` внутри domain, `canonical_name` и aliases. Glossary/AI resolution может
связать английское и русское названия одного алгоритма; неоднозначные кандидаты остаются
отдельными до явного user merge. AI dedup использует multilingual embeddings и существующие
пороги только как optional suggestion/confirmation policy, а не как жёсткую ontology.

## 4. Graph write

Разрешены generic node/edge properties и kinds. Проверяются только технические условия:
корректный endpoint, domain, provenance и отсутствие опасных операций. `_validate_ontology`,
profile edge whitelist, `ensure_schema` и Neo4j constraints не являются ingest-гейтом. Фактический
состав рёбер и набор видов — `data_model.md` §3.

## 5. Retrieval pipeline

```text
baseline: vector search → metadata sources → rerank/context assembly
optional graph experiment: vector seeds → bounded graph expansion → boost/rerank → context assembly
```

Vector-only baseline не вызывает graph. Graph experiment получает seeds из vector metadata,
выполняет ограниченный traversal и сохраняет path/depth/confidence для eval. При недоступном,
неполном или устаревшем graph используется vector-only fallback с degraded marker.
