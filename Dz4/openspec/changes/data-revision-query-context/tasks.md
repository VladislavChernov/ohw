# Задачи

## Ingestion-контур

- [ ] `DocumentRegistry.data_revision(domain) -> str | None`: `sha256` по
      отсортированным `content_hash` активных документов домена (`STATUS_ACTIVE`);
      пустой домен → `None`.
- [ ] Endpoint `GET /api/v1/ingestion/revision?domain=` (X-API-Key):
      `{"revision": ..., "updated_at": ...}`; без `domain` → 422.
- [ ] Тесты: `test_ingestion_revision.py` — no-op INGEST не меняет rev; добавление/
      удаление меняет; сортировка хэшей независима от порядка; пустой → None;
      401 без ключа; 422 без domain.

## Semantic Cache (epoch-bump префикс)

- [ ] `SemanticCache.lookup/store` + конкретные реализации принимают
      `revision: str | None = None`.
- [ ] Redis: ключ `query:sc:<rev>:<domain>` при известной ревизии, иначе
      `query:sc:<domain>` (обратная совместимость); meta-ключ счётчиков —
      `query:sc:<rev>:<domain>:meta` (корректно поправлен и в `stats()`/`clear()`).
- [ ] InMemory: бакет ключуется `(domain, revision)`, старые эпохи держатся до TTL.
- [ ] Тесты: store(rev A) → hit(rev A), miss(rev B); Redis-ключ с префиксом rev;
      статистика переживает epoch-bump.

## Pipeline и worker

- [ ] `QueryPipeline.run(query, domain, emit, revision=None)` — передаёт ревизию в
      lookup/store, `done.revision` (и в cache-hit-пути а также).
- [ ] `RevisionClient` (`query_service/revision_client.py`): per-domain кэш
      (revision, updated_at, last_poll), интервал `REVISION_POLL_INTERVAL_S`,
      fail-open (сбой → last known/None), счётчик ошибок поллера (
      `revision_poll_errors_total`, паттерн S6/`topology_poll_errors_total`),
      `from_env()`.
- [ ] Снапшот метрик воркера (`_log_metrics_snapshot`) включает
      `revision_poll_errors_total` и известные текущие ревизии доменов —
      видимость свежести для оператора (ревью-07, вывод 3).
- [ ] Worker: `process_one` запрашивает `revisions.revision(task.domain)`,
      передаёт в `pipeline.run`; интервал поллинга и таймаут из env;
      при пустом/false `REVISION_POLL_INTERVAL_S` поллер выключен (M3-поведение).
- [ ] `runtime.py`: wiring `RevisionClient` в `QueryWorker` (worker main).
- [ ] Тесты worker: фейк-клиент → done несёт ревизию; недоступный ingestion не
      роняет обработку; интервал уважается (не более 1 запроса в окно).

## Документация

- [ ] `docs/invariants.md` v9: L2-07 → реализован (снять «planned»); bounded
      staleness сформулирован как L2-07 с указанием окна ≤ интервал поллинга.
- [ ] Выровнять нумерацию L2-04/L2-07 во всех файлах — SC-13:
      `docs/prototype_requirements.md` (L2-04→L2-07 для bounded staleness),
      ADR-026 последствия, `learning/data_revision_analytics.md`.
- [ ] ADR-026: статус → реализован (последствия фактических решений).
- [ ] `docs/prototype_requirements.md`: чеклист Вехи 4-хвост — отмечен
      (кроме M4-Eval), история в `docs/history.md`.
- [ ] Финальный прогон `ruff` + `mypy` + `pytest`; коммит, push, CI green.