# Proposal: Offline graph projection lifecycle

## Почему

Текущий lightweight baseline уже умеет принимать документ и сохранять vector metadata независимо от graph, но lifecycle графа пока не имеет отдельного состояния готовности и revision. Поэтому нельзя надёжно отличить отсутствующую, частично построенную или устаревшую projection от успешной, а offline rebuild и backfill vector metadata не имеют идемпотентного job-контракта.

Этот change является follow-up к `add-lightweight-context-graph`. Он не возвращает typed ontology, не делает graph обязательным для ingest и не изменяет vector-only ranking. Он добавляет только управляемый lifecycle optional projection: state, revision, offline rebuild, metadata backfill и явный readiness gate для experiment retrieval.

## Что делаем

- Вводим domain-scoped `ProjectionState` со статусом `pending`, `ready`, `degraded`, `stale` или `failed`, input/data revision, projection revision, config fingerprint и временем последней попытки.
- Добавляем идемпотентный offline enrichment/rebuild job с lease/claim, детерминированным job key и безопасным повтором после частичного сбоя.
- Разделяем операции graph projection и backfill `context_ids`/`tag_ids` в vector metadata; обе операции могут повторяться без изменения document content и без дублей provenance.
- Определяем readiness gate: graph experiment запускается только для `ready` projection с актуальной revision; `pending`, `degraded`, `stale`, `failed` и отсутствующее состояние дают vector-only fallback с degraded marker.
- Включаем projection revision в semantic-cache key и eval manifest/trace, чтобы fallback не выдавался за успешный graph result.
- Сохраняем независимость `GraphStoreProvider`, `VectorStoreProvider` и projection state store; Neo4j остаётся одним backend-примером, а не обязательной зависимостью.
- Добавляем операционные метрики и audit record: domain, job id, input revision, projection revision, status, duration, processed/skipped/failed sources и last error.

## Спека

- `spec.md` — сценарии состояния projection, идемпотентного rebuild, backfill, stale/failed fallback, domain isolation и retry.
- `design.md` — границы подсистем, модель revision, порядок job, readiness gate и стратегия частичного сбоя.
- Нормативные `CONCEPT.md`, `docs/01`, `docs/02`, `docs/03`, `docs/data_model.md`, `docs/invariants.md` и eval-документация обновляются только после принятия этого change.
- До завершения review и approval change считается ненормативным proposal-артефактом; код и runtime не изменяются в рамках proposal.

## Проверка

1. Сначала добавить named stored regression-тесты для state transitions, idempotent rebuild, backfill, stale/failed fallback и revision/cache isolation.
2. Проверить targeted и полный `pytest`, `ruff` и `mypy` в persistent dev-контейнере с host `RUN_CODE_COMMIT`.
3. Проверить, что vector-only baseline и ingest не требуют projection state store, graph adapter или успешного rebuild job.
4. Live Neo4j и paired eval остаются отдельным follow-up; этот change не требует запуска live-сервисов для базовой приёмки.

## Вне scope

- Полноценный transactional outbox, распределённая координация и production HA.
- Массовая миграция старой typed-ontology базы и удаление старых constraints.
- Новый публичный UI для управления projection jobs.
- Новый обязательный backend для graph или vector storage.
