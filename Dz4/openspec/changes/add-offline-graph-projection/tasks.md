# Задачи: Offline graph projection lifecycle

> Сначала фиксируется контракт state/revision, затем реализуется job и интеграция readiness. Live Neo4j и paired eval остаются отдельной приёмкой.

- [x] LP-01. Зафиксировать `ProjectionState`, projection revision, config fingerprint и допустимые status transitions.
- [x] LP-02. Определить state-store API: read, compare-and-set, lease и job key без привязки к Neo4j.
- [x] LP-03. Добавить named stored tests для state transitions, stale/ready переходов и domain isolation.
- [x] LP-04. Реализовать идемпотентный offline rebuild claim/lease и обработку active domain sources.
- [x] LP-05. Реализовать generic graph projection batch с Source/Chunk anchors, provenance и manual/AI precedence.
- [x] LP-06. Реализовать vector metadata backfill `context_ids`/`tag_ids` без изменения content, embedding и document registry.
- [x] LP-07. Добавить verification counts, partial-failure state и безопасный retry/backfill marker.
- [x] LP-08. Подключить readiness gate к QueryPipeline: ready → graph experiment, иначе vector-only degraded fallback.
- [x] LP-09. Разделить cache keys по projection revision и исключить degraded fallback из graph-success cache/eval.
- [x] LP-10. Добавить audit record и метрики job/state/stale/fallback без логирования payload/secrets.
- [x] LP-11. Обновить нормативные docs и OpenSpec task references после реализации.
- [x] LP-12. Проверить targeted/full `pytest`, `ruff` и `mypy` в persistent dev-контейнере с host `RUN_CODE_COMMIT`.
- [ ] LP-13. Провести отдельную live Neo4j parity и paired eval проверку после approval; не смешивать её с базовой DoD.
  - [x] Live Neo4j parity: `tests/test_live_projection_neo4j_parity.py` (маркер `live`, изолированный домен `lp13`), 5/5 passed на neo4j:5.26. Найдены и исправлены три невалидных Cypher-конструкции (`list - list`, `NOT value IN $list`) в `neo4j.py` — они ломали и ingest-путь.
  - [ ] Paired eval `--mode both` на eval-minimal стенде: требует отдельного approval, GPU-профиля и ~4 ГБ моделей в volume.
