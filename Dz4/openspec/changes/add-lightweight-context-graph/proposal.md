# Proposal: Primitive ingest и optional offline context graph experiment

> **Актуализировано 2026-09-26.** Бандл объявлен активным lifecycle-контрактом в
> `openspec/project.md` и обновлён на месте. Часть правок контракта была сначала зафиксирована в
> коде, а уже потом в этом бандле — нарушение порядка spec-first из `openspec/project.md`.
> Расхождение зафиксировано здесь явно, а не сглажено: коммиты `467dbe3`, `6e6f3e3`, `79b7123`
> в ветке `pre-eval-docs-code-alignment`. Существо исправлений — снятие фильтра видов рёбер при
> обходе и приведение документации к фактическому поведению; см. `docs/data_model.md` §3 и §3.1
> и инвариант `L3-02a`.

## Почему

Текущая реализация превратила Domain Profile в обязательную typed ontology: `_validate_ontology`,
`ensure_schema`, фиксированные labels/edge types и Neo4j constraints. Это не соответствует цели
прототипа — проверить, насколько лёгкая графовая связность ускоряет поиск.

Целевая модель:

- ingest документа и chunks работает всегда и не зависит от наличия AI или полной онтологии;
- graph enrichment опционален: пользователь может не задавать теги, AI может предложить их;
- граф хранит разреженные контекстные узлы и связи без жёсткой семантической схемы;
- vector retrieval с metadata (`text`, `source_id`, `chunk_id`, `domain`, `revision`,
  `context_ids`/`tag_ids`) остаётся самостоятельным baseline;
- graph — отдельный optional experiment: его можно построить inline после primitive
  ingest или отдельным offline replay, не меняя vector baseline;
- experiment retrieval использует готовый graph projection только для bounded expansion;
  при отсутствии/отставании индекса работает vector-only fallback.

Lifecycle графа допускает два запуска:
1. **Inline enrichment** — best-effort стадия после vector commit; ошибка не отменяет документ.
2. **Offline enrichment/rebuild** — отдельный идемпотентный replay корпуса, который строит graph
   projection и backfill-ит metadata позже.

Мультиязычные названия не должны превращаться в разные теги только из-за языка. Термин имеет
стабильный `tag_id` внутри домена, `canonical_name` как preferred label и `aliases`, включая
переводы/варианты записи. Glossary и optional AI resolution помогают связать варианты; при
неоднозначности сохраняются отдельные теги и предлагается merge.

## Что делаем

- Ввести минимальный динамический контракт `ContextNode`/`ContextEdge` без обязательных
  `Requirement|Concept|Contract`, `unique_key` и Cypher validation.
- Сделать tags/links необязательными для ingestion API; поддержать ручной и AI источники в
  одном graph-представлении с provenance (`origin`, `confidence`, `source_ids`).
- Убрать runtime `_validate_ontology`, `ensure_schema` и DDL constraints из ingest path.
- Сохранить `Source`/`Chunk` как технические anchors, chunk/vector linkage и soft-delete.
- Ввести vector-first **experiment retrieval**: metadata возвращает `context_ids`; готовый
  graph adapter выполняет bounded expansion по рёбрам любого вида в обе стороны
  (`retrieval.expansion_direction`), с configurable depth/fanout/budget; результат получает
  ограниченный boost.
- Сохранить независимость `GraphStoreProvider` и `VectorStoreProvider`; Neo4j — только
  прототиповый backend, не обязательная архитектурная привязка.
- Переписать eval-контракт: baseline = vector-only; experiment = vector + готовый graph
  expansion; измерять исходный vector rank, seed chunks, paths, boost, recall/latency и
  graph contribution. Если graph projection не готов, это отдельный degraded baseline, а не
  успешный graph experiment.
- Typed-ontology часть текущего `eval-graph-contribution-experiment` пометить superseded;
  исторические документы не удалять, а зафиксировать новую корректирующую линию.

## Спека

- `spec.md` — обязательные сценарии primitive ingest, optional tags/links, multilingual tag
  identity, bounded expansion и fallback.
- `design.md` — границы подсистем, модель данных и миграционная стратегия без тяжёлой ontology.
- Нормативные `CONCEPT.md`, `docs/01`, `docs/02`, `docs/03`, `docs/data_model.md`,
  `docs/glossary.md`, `docs/invariants.md`, `docs/adapters_specification.md` и
  `docs/prototype_requirements.md` должны быть синхронизированы с этим контрактом.
- `docs/05_adr_log.md` получает ADR о lightweight context graph; прежние typed-ontology
  утверждения остаются историческими и помечаются superseded.

## Проверка

1. Stored unit-тесты: ingest без profile/AI/ontology; optional user tags/links; dynamic edge
   types; multilingual alias resolution; no-op/retention; metadata preservation.
2. Retrieval-тесты: baseline vector-only и experiment vector seeds → bounded expansion;
   depth/fanout/budget; boost; fallback; arbitrary properties; graph/vector adapter parity.
3. Eval-тесты: baseline/target manifest parity, graph path/boost trace, contribution metrics.
4. `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src` в persistent
   dev-контейнере с host `RUN_CODE_COMMIT`.
5. Live Neo4j/eval и migration старых constraints не входят в DoD этого corrective change.

## Вне scope

- Полноценный transactional outbox, dual-generation и production migration — уже отложены на
  M6-Growth / pre-connectors.
- Удаление constraints/старых typed-узлов из существующей Neo4j базы.
- Обязательный UI-редактор облака тегов; API/storage-контракт должен допускать ручной режим,
  но полный UX — отдельная задача.
