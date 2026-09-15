# Proposal: Ревизия данных в query-контуре (Веха 4-хвост, L2-07)

## Почему

Семантический кэш живёт на TTL + ручном `clear()` (допущение ADR-025). У «актуального
контекста ответа» нет fingerprint-а знаний: Eval (ADR-015) не может зафиксировать
«по каким данным собран ответ», а после COMMIT старого документа ответ может
выдаваться из кэша до истечения TTL. L2-07 «Bounded staleness» по-прежнему *planned*.

Решение по свежести уже принято (ADR-026, 2026-09-14): не инвалидация кэша, а экспорт
существующих версий DocumentRegistry (ADR-014) в query-контур. Веха 4-хвост в
`docs/prototype_requirements.md` — обязательна ДО M5/M6.

## Что делаем

- Ingestion: `DocumentRegistry.data_revision(domain) -> str | None` —
  `sha256(sorted(content_hash ОБ активных документов домена))`; идемпотентен (no-op
  INGEST не меняет), честен к коллекции источников, переиспользует поля ADR-014.
- Ingestion: `GET /api/v1/ingestion/revision?domain=` (X-API-Key, L5-01)
  → `{"revision": <hex|None>, "updated_at": <макс created_at активных|None>}`.
- Query: `RevisionClient` (аналог `TopologyClient`) — GET `{INGESTION_URL}/api/v1/ingestion/revision?domain=`,
  кэш revision/updated_at **по домену** с интервалом `REVISION_POLL_INTERVAL_S`
  (дефолт 5; 0 — поллер выключен, M3-поведение), fail-open (сбой → последняя
  известная ревизия; первый сбой с самого старта → `None`).
- Semantic Cache: `lookup/store` принимают `revision: str | None = None`;
  Redis-ключ `query:sc:<rev>:<domain>` (revision) либо `query:sc:<domain>` (None —
  эпоха по умолчанию, обратная совместимость); InMemory-бакет ключуется
  `(domain, revision)`. Старые эпохи не достаются (неток-se search) и дочищаются TTL.
- Pipeline: `run(query, domain, emit, revision=None)` → ревизия уходит в lookup/store;
  в `done` добавляется `revision` (str | None) — fingerprint среза для Eval (ADR-015)
  и явный bounded staleness (L2-04).
- Worker: поллер ревизий по потребности — перед каждым `process_one` вызывается
  `revisions.revision(task.domain)` (per-domain refresh не чаще интервала; задержка
  bump ≤ интервал поллинга). Никакого статического списка доменов не требуется.
- Env: `INGESTION_URL` (дефолт `http://ingestion:8002`), `REVISION_POLL_INTERVAL_S`,
  `REVISION_TIMEOUT_S` (дефолт 3.0), ключ — `AUTH_API_KEY`/`GRAPH_AUTH_API_KEY`.
- Инварианты: `L2-07` → реализован (снять «planned»); SC-13 — нумерация
  bounded staleness выровнена на L2-07 во всех файлах (`invariants.md`,
  `prototype_requirements.md`, ADR-026, `learning/data_revision_analytics.md`);
  ADR-026 отмечается как реализованный; чеклист Вехи 4-хвост в
  `prototype_requirements.md` закрывается (кроме M4-Eval, отдельная веха).
- Наблюдаемость (S6/вывод 3 ревью-07): `RevisionClient` считает
  `revision_poll_errors_total` (сбой ingestion не молчит); снапшот метрик воркера
  несёт этот счётчик и известные текущие ревизии доменов — оператор отвечает на
  «какая ревизия сейчас в query-контуре» без захода в контейнер.

## Спека

`specs/data-revision-query-context/spec.md` — контракты `data_revision`, endpoint
`/revision`, `RevisionClient`, сигнатуры кэша с `revision=true`, формат `done`,
env и дефолты, границы гарантии (bounded staleness, задержка ≤ интервал).

## Проверка

1. Юнит: `test_ingestion_revision.py` — `data_revision` не меняется на no-op,
   меняется при добавлении/удалении, пустой домен → None; endpoint 200/401/422.
2. Юнит кэша: `test_semantic_cache.py` — store(rev A) → lookup(rev A) hit,
   lookup(rev B) miss (epoch-bump); Redis-ключ `query:sc:<rev>:<domain>`.
3. Pipeline: `test_retrieval_pipeline.py` — `done` несёт `revision`; cтore/lookup
   получают ревизию (mock-кэш).
4. Worker: `test_worker_revision.py` — `RevisionClient` (фейк) подставляется в
   `process_one`, done содержит ревизию; интервал и fail-open (недоступный
   ingestion не роняет обработку).
5. Полный `pytest` + `ruff` + `mypy` чисто; CI green.