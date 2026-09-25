# Документация: Ingestion Pipeline и Normalizer v3

> **Статус:** vector-only baseline + optional offline graph experiment

## 1. Primitive pipeline

1. **INGEST** — reader создаёт canonical `Document` из txt/md/pdf; profile и AI не требуются.
2. **CHUNK** — текст режется выбранным `Chunker`; профиль может дать optional hints, но
   недоступный профиль не блокирует дефолтный chunker.
3. **EMBED** — выбранный Embedder вычисляет vectors. Ошибка graph enrichment не влияет на этот шаг.
4. **VECTOR COMMIT** — Source/Chunk anchors, text, embeddings и provenance записываются в
   vector/object storage. Это завершает baseline ingest.
5. **GRAPH PROJECTION (optional experiment)** — manual tags/links, AI candidates и glossary
   aliases сохраняются как dynamic context nodes/edges. Projection может быть построена inline
   после vector commit или отдельным offline replay; пустой enrichment допустим.

Ошибка optional AI enrichment сохраняется как degraded/quarantine status и не превращает
primitive ingest в failed job, если не запрошен strict enrichment mode. Offline job также не
изменяет активный vector baseline: он строит экспериментальную projection и может backfill-ить
`context_ids`/`tag_ids` в metadata.

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

Разрешены generic node/edge properties и kinds (`parent`, `related`, `mentions`). Проверяются
только технические условия: корректный endpoint, domain, provenance и отсутствие опасных
операций. `_validate_ontology`, profile edge whitelist, `ensure_schema` и Neo4j constraints не
являются ingest-гейтом.

## 5. Retrieval pipeline

```text
baseline: vector search → metadata sources → rerank/context assembly
optional graph experiment: vector seeds → bounded graph expansion → boost/rerank → context assembly
```

Vector-only baseline не вызывает graph. Graph experiment получает seeds из vector metadata,
выполняет ограниченный traversal и сохраняет path/depth/confidence для eval. При недоступном,
неполном или устаревшем graph используется vector-only fallback с degraded marker.
