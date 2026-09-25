# Design: Offline graph projection lifecycle

## 1. Границы

Система разделяется на четыре независимых слоя:

1. **Primitive/vector baseline** — document registry, chunks, embeddings и vector metadata. Этот слой не зависит от graph projection state.
2. **Projection state** — небольшой domain-scoped record о том, какая projection построена, для какой data revision и с каким config fingerprint.
3. **Offline projection job** — идемпотентное построение или обновление generic graph nodes/edges и backfill vector metadata.
4. **Graph experiment retrieval** — vector-first retrieval с readiness gate; при неготовой или stale projection возвращается vector-only fallback.

Projection state store и graph store не объединяются в одну транзакцию с vector store. Идемпотентность достигается детерминированными IDs, сравнением revision, lease и безопасным повтором отдельных шагов.

## 2. Модель состояния

```text
ProjectionState {
  domain: string,
  data_revision: string,
  projection_revision: string,
  config_fingerprint: string,
  status: pending | ready | degraded | stale | failed,
  job_id: string | null,
  input_source_count: int,
  processed_source_count: int,
  skipped_source_count: int,
  failed_source_count: int,
  started_at: string,
  finished_at: string | null,
  last_error: string | null
}
```

`data_revision` — fingerprint активного document set. `projection_revision` — fingerprint результата projection: input data revision, enrichment/config fingerprint и версии алгоритмов. Он не зависит от порядка обработки источников.

State store должен поддерживать read, compare-and-set и lease. Конкретный backend выбирается реализацией, но API не зависит от Neo4j или SQLite.

## 3. Lifecycle job

```text
claim lease
  → load active documents for domain
  → compute input/config revisions
  → compare with current state
  → build graph projection in deterministic batches
  → backfill vector metadata
  → verify counts and provenance
  → compare-and-set state=ready
```

Job key состоит из `domain`, `data_revision`, `projection_revision` и `config_fingerprint`. Повторный запуск того же key не создаёт дубли и либо возвращает уже готовый результат, либо продолжает незавершённую работу после lease expiry.

Graph nodes/edges, `Source`/`Chunk` anchors, `MENTIONS`, `origin`, `confidence`, `source_ids` и `chunk_ids` записываются идемпотентно. Backfill меняет только optional metadata (`context_ids`, `tag_ids`, projection metadata) и не меняет content, document registry content hash или embedding.

Если graph batch или backfill завершился частично, state становится `degraded` или `failed`, а baseline остаётся валидным. Следующий запуск с тем же key должен безопасно продолжить/повторить незавершённые source units.

## 4. Readiness и stale

`ready` означает, что state существует, `data_revision` совпадает с текущей revision, `projection_revision` совпадает с вычисленным fingerprint и verification прошёл.

`stale` означает, что текущая data revision новее projection revision. `pending`, `degraded`, `failed` и отсутствие state также запрещают graph experiment. В этих случаях QueryPipeline возвращает vector-only fallback, `graph_degraded=true` и причину в trace/eval; fallback не кэшируется как успешный graph result.

Семантический cache использует vector baseline отдельно от graph experiment и включает `projection_revision` для graph cache. До появления готовой projection graph cache не используется.

## 5. Границы отказов

- Ошибка state store не должна ломать primitive ingest; vector baseline и job status сообщают ошибку отдельно.
- Ошибка graph adapter не должна откатывать уже подтверждённый vector commit.
- Ошибка backfill не должна удалять или перезаписывать vector body; vector metadata остаётся в предыдущем валидном состоянии и получает retry marker.
- Lease истечение не должно приводить к двум активным writers: следующий claimant сначала проверяет state и job key.
- Ошибка одного domain не изменяет state другого domain.

Оператор может изменить `projection.lease_seconds` через Configurator
(`GET/PUT /api/v1/config/projection`); backend `ProjectionStateStore` и SQLite
не являются операторскими настройками. Lease продлевается job'ом после каждого
source unit; CLI-флаг lease не используется.

## 6. Наблюдаемость и операции

Каждая попытка job пишет audit record и метрики: `projection_job_total`, `projection_job_duration`, `projection_sources_processed`, `projection_sources_failed`, `projection_state_total`, `projection_stale_total` и `projection_fallback_total` с domain label. Оператор может повторить failed job без изменения document content.

Live Neo4j и paired eval не являются условием базовой приёмки state/job API; они проверяются отдельным follow-up после прохождения unit/in-memory контрактов.
