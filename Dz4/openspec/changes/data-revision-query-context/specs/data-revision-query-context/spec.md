# Spec: Ревизия данных в query-контуре (Веха 4-хвост, L2-07)

## Ingestion

`DocumentRegistry.data_revision(domain: str) -> str | None`:
- hash: `sha256` над конкатенацией пар `(source_url, content_hash)` активных документов
  домена (`status = active`), отсортированных по возрастанию (байт-порядок);
- `None`, если активных документов у домена нет;
- идемпотентен: повторный INGEST без изменения `content_hash` (no-op, ADR-014) не
  меняет результат; изменение/добавление/soft-delete — меняет.

Endpoint `GET /api/v1/ingestion/revision?domain=<domain>` (X-API-Key, L5-01):
```
200 {"revision": "<hex>|null", "updated_at": "<ISO8601>|null"}
422 — отсутствует query-параметр `domain`
401 — неверный/отсутствующий X-API-Key
```
`updated_at` — максимальный `created_at` активных документов (None при пустом домене).

## Semantic Cache (ADR-025 + ADR-026)

Сигнатуры: `lookup(embedding, threshold, domain, revision: str | None = None)`,
`store(embedding, answer, domain, revision: str | None = None)`.

Ключевое пространство:
- ревизия известна: Redis-ключ `query:sc:<rev>:<domain>`; meta-ключ счётчиков
  `query:sc:<rev>:<domain>:meta` (HINCRBY hits/misses);
- ревизия `None` (не доставлена): `query:sc:<domain>` / `query:sc:<domain>:meta`
  (эпоха по умолчанию — M3-поведение);
- InMemory: бакет `(domain, revision)`.

Правила:
- hit/miss считаются в той эпохе, по которой выполнен lookup;
- bump ревизии атомарно переводит поиск на новый ключ; старая эпоха не читается,
  дочищается TTL (Redis) / живёт до TTL (InMemory);
- «плохие» ответы по-прежнему не кэшируются (`should_cache_text`);
- fail-open на сбой Valkey сохраняется (lookup → miss, store → no-op), счётчики
  сбойных путей не увеличиваются.

## QueryPipeline

`run(query, domain, emit, revision: str | None = None) -> dict`:
- `revision` передаётся в `semantic_cache.lookup/store`;
- `done` в любом исходе несёт `revision` (str | None) — fingerprint среза для Eval;
- cache-hit-путь возвращает сохранённую ревизию эпохи (той, под которой был store),
  НЕ текущую — двух ревизий в одном ответе не бывает.

## RevisionClient (query-воркер)

- `revision(domain) -> str | None`: возвращает закэшированную ревизию домена, если
  `now - last_poll < REVISION_POLL_INTERVAL_S`, иначе делает GET
  `{INGESTION_URL}/api/v1/ingestion/revision?domain=<domain>` (timeout
  `REVISION_TIMEOUT_S`);
- fail-open: сбой → последняя известная ревизия домена; первый сбой с самого старта
  → `None`; воркер не падает;
- каждый сбой поллера инкрементирует `revision_poll_errors_total` (паттерн
  S6/`topology_poll_errors_total`) — деградация не молчит;
- `REVISION_POLL_INTERVAL_S` (дефолт 5.0; `0`/нечисловое → поллер выключен, все
  ревизии `None` — M3-поведение);
- ключ: `AUTH_API_KEY` / `GRAPH_AUTH_API_KEY` (дефолт `changeme`).

## Метрики снапшота воркера

- `_log_metrics_snapshot` дополняется `revision_poll_errors_total` и известными
  текущими ревизиями доменов (`{domain: revision}`) — оператор видит «какая
  ревизия сейчас в query-контуре» (ревью-07, вывод 3) без захода в контейнер.

## Worker

- `process_one`: ревизия задачи = `revisions.revision(task.domain)`, передаётся в
  `pipeline.run`; ревизия не участвует в fencing (только в кэш и fingerprint).
- Задержка между COMMIT и появлением новой эпохи в ответах ≤ интервал поллинга.

## Гарантии и границы

- Bounded staleness (L2-04): ответ свеж относительно данных тогда и только тогда,
  когда `done.revision` == текущей `rev<domain>`; иначе ответ собран по данным
  предшествующей эпохи (генерируется заново после bump).
- Прямой инвалидации «по источникам» и «как в индексе» нет (ADR-025) — только
  epoch-bump + TTL-доочистка.
- Изоляция доменов сохраняется (L1-01): bump `it` не трогает `legal`.