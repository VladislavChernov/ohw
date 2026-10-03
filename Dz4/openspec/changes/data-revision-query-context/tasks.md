# Задачи

## Ingestion-контур

- [x] `DocumentRegistry.data_revision(domain) -> str | None`: `sha256` по
      отсортированным `content_hash` активных документов домена (`STATUS_ACTIVE`);
      пустой домен → `None`.
- [x] Endpoint `GET /api/v1/ingestion/revision?domain=` (X-API-Key):
      `{"revision": ..., "updated_at": ...}`; без `domain` → 422.
- [x] Тесты: `test_ingestion_revision.py` — no-op INGEST не меняет rev; добавление/
      удаление меняет; сортировка хэшей независима от порядка; пустой → None;
      401 без ключа; 422 без domain.

Проверено: `storage/registry.py:187` (`sha256` над `sorted(set((source_url,
content_hash)))`, пусто → `None`), `app.py:479` (маршрут, `require_key`, 422 на
отсутствии `domain`), `test_ingestion_revision.py:33,39,53,72,80,88,102,110,117`.
Формула сверена с `design.md:14`: там `sha256(sorted (source_url, content_hash))`,
так что код совпадает с решением, а `content_hash` в этой строке — сокращение
пересказа, а не другое решение. Отдельные тесты на идентичность источника
(`test_data_revision_includes_source_identity`) и по-доменную изоляцию сверх списка,
но по тому же инварианту.

## Semantic Cache (epoch-bump префикс)

- [x] `SemanticCache.lookup/store` + конкретные реализации принимают
      `revision: str | None = None`.
- [x] Redis: ключ `query:sc:<rev>:<domain>` при известной ревизии, иначе
      `query:sc:<domain>` (обратная совместимость); meta-ключ счётчиков —
      `query:sc:<rev>:<domain>:meta` (корректно поправлен и в `stats()`/`clear()`).
- [x] InMemory: бакет ключуется `(domain, revision)`, старые эпохи держатся до TTL.
- [x] Тесты: store(rev A) → hit(rev A), miss(rev B); Redis-ключ с префиксом rev;
      статистика переживает epoch-bump.

Проверено: `semantic_cache.py:105,118` (ABC), `:168,189` (InMemory), `:316,372`
(Redis); ключи и meta — `:281-293`, `stats()` агрегирует по всем эпохам через SCAN
`query:sc:*` (`:389`), `clear()` сметает и их (`:414`); бакет — `:180,197`.
Тесты `test_semantic_cache.py:277,284,292,298,306,314,322`.

## Pipeline и worker

- [x] `QueryPipeline.run(query, domain, emit, revision=None)` — передаёт ревизию в
      lookup/store, `done.revision` (и в cache-hit-пути а также).
- [x] `RevisionClient` (`query_service/revision_client.py`): per-domain кэш
      (revision, updated_at, last_poll), интервал `REVISION_POLL_INTERVAL_S`,
      fail-open (сбой → last known/None), счётчик ошибок поллера (
      `revision_poll_errors_total`, паттерн S6/`topology_poll_errors_total`),
      `from_env()`.
- [x] Снапшот метрик воркера (`_log_metrics_snapshot`) включает
      `revision_poll_errors_total` и известные текущие ревизии доменов —
      видимость свежести для оператора (ревью-07, вывод 3).
- [x] Worker: `process_one` запрашивает `revisions.revision(task.domain)`,
      передаёт в `pipeline.run`; интервал поллинга и таймаут из env;
      при пустом/false `REVISION_POLL_INTERVAL_S` поллер выключен (M3-поведение).
- [x] `runtime.py`: wiring `RevisionClient` в `QueryWorker` (worker main).
- [x] Тесты worker: фейк-клиент → done несёт ревизию; недоступный ingestion не
      роняет обработку; интервал уважается (не более 1 запроса в окно).

Проверено: `pipeline.run(..., revision=...)` и `done.revision` — `worker.py:99-115`,
`retrieval/pipeline.py` (прокидывание в lookup/store, `done` в hit и generate);
`revision_client.py` — пер-доменный кэт `(revision, updated_at, last_poll)` (`:51`),
окно и fail-open (`:96-112`), счётчик (`:52,106`), `from_env` (`:54`);
снапшот — `worker.py:189-193`; интервал/таймаут из env — `REVISION_POLL_INTERVAL_S`
и `REVISION_TIMEOUT_S`.

Исправлено при применении бандла:

- **Задача 11 была закрыта наполовину.** `REVISION_POLL_INTERVAL_S` разбирался через
  `_env_float`, который на `float("")` и `float("false")` возвращал дефолт 5.0, то есть
  объявленное «поллер выключен» означало «поллер включён». Тесты закрывали только `{}`
  → 5.0 и `"0"` → 0.0, поэтому расхождение с текстом бандла было невидимо. Разбор
  разделён на три состояния (`revision_client.py:120`): не задана → дефолт; пусто /
  `0` / `false` / `off` / `no` / `none` → поллер выключен; неразбираемая строка →
  дефолт, потому что это ошибка конфигурации, а не команда «выключено». Гард:
  `test_revision_client.py` (параметризованный `test_from_env`).
- **Задача 13 не была проверена на уровне воркера.** Все три теста воркера
  подставляли `_StubRevisions` с фиксированным ответом, то есть путь «transport
  ingestion упал» не проходил ни один тест — ни клиентский на настоящем
  `ConnectionError`, ни воркерский. Добавлен
  `test_query_api.py::test_worker_survives_unreachable_ingestion`: настоящий
  `RevisionClient` с транспортом, который всегда отказывает, и проверка всего пути
  до `succeeded` + `revision=None` + `revision_poll_errors_total == 1`. Дубль для
  этого не годился: дубль повторил бы поведение клиента, а не гарантию.
- **Задача 12 указывала не тот файл.** Wiring-а в `runtime.py` нет и не требуется:
  `RevisionClient.from_env()` не зависит ни от одного блока сборки, который собирает
  `runtime.py`. Реализация — `worker.py:261,270` (`RevisionClient.from_env()` →
  `QueryWorker(revisions=...)` в `main()`), что и описывает `design.md:18`
  («Query Worker (loop) → RevisionClient»). Отметка проставлена по факту
  реализации, а не по указанному в задаче пути.
- Остальное проверено: `test_query_api.py:220` (фейк-клиент, ревизия доезжает до
  `run`), `:257` (активный домен разрешается до запроса ревизии), `:291` (снапшот
  содержит `revision_poll_errors_total` и `revisions`); окно поллера —
  `test_revision_client.py:47` (два вызова подряд → один GET) и `:77` (повторный
  опрос после истечения окна).

## Документация

- [x] `docs/invariants.md` v9: L2-07 → реализован (снять «planned»); bounded
      staleness сформулирован как L2-07 с указанием окна ≤ интервал поллинга.
- [x] Выровнять нумерацию L2-04/L2-07 во всех файлах — SC-13:
      `docs/prototype_requirements.md` (L2-04→L2-07 для bounded staleness),
      ADR-026 последствия, `analitic/revision/data_revision_analytics.md`.
- [x] ADR-026: статус → реализован (последствия фактических решений).
- [x] `docs/prototype_requirements.md`: чеклист Вехи 4-хвост — отмечен
      (кроме M4-Eval), история в `docs/history.md`.
- [x] Финальный прогон `ruff` + `mypy` + `pytest`; коммит, push, CI green.

  Прогон выполнен: ruff чист, mypy — 73 файла без замечаний, `pytest -q` → **1020 passed,
  5 skipped, 2 deselected**. Тот же набор команд, что выполняет `.github/workflows/ci.yml`.
  Формулировка «гейт зелёный» здесь означала бы больше, чем показано: это один прогон на
  момент записи, а гейт был нестабилен (см. абзац о стабильности ниже). После правки класса
  ожидания — два зелёных прогона подряд, 1027 passed; это свидетельство, не гарантия.

  Про «CI green» честно: у workflow триггер — push в `master` и pull request, поэтому
  с ветки `pre-eval-docs-code-alignment` зелёный CI не наблюдаем (и `gh` в окружении нет).
  Локальный гейт — более слабое утверждение, чем CI: он не покрывает `ubuntu-latest`,
  свежий `uv sync --group dev` и порядок установки пакетов. Пункт закрыт локальным
  прогоном, а не наблюдением CI; при следующем PR это утверждение станет проверяемым.

  Отдельно о стабильности прогона. Первые три полных прогона дали один падающий: два теста
  ожидания джобы (`test_ingest_accepts_optional_tags_and_links` и один из `wait_until` в
  `test_ingestion_api.py`). Причина установлена: они ждут завершения джобы по стенным часам —
  5 с и 10 с, — и на медленном прогоне (713 с против 287 с) не укладываются. Класс покрывал
  четыре места (`test_lightweight_context_graph.py:259` — 5 с, `wait_until` в
  `test_ingestion_api.py:31` — 10 с, `test_unresolved_endpoint_fact.py:353` — 10 с,
  `test_prompt_renderer.py:151` — 60 с).

  Класс закрыт (`tests/job_wait.py`): инвариант проверки — «джоба дошла до терминального
  статуса», а не «уложилась в N секунд»; потолок 120 с остаётся конечным, а по его
  исчерпании наружу отдаётся последнее наблюдённое состояние и прошедшее время, так что
  падение остаётся падением, а не превращается в «не успел — значит не заметил».
  `tests/test_job_wait.py` (7 тестов) фиксирует именно этот контракт, включая случай
  «`failed` — терминальный статус» и «потолок исчерпан, но исключения нет».

  Свидетельство после правки: **два полных прогона подряд — 1027 passed, 5 skipped,
  2 deselected, 0 FAILED**, стенд при этом намеренно поднят (нагруженное условие, на котором
  тест падал), плюс те же четыре файла под нагрузкой 8 процессов на 16 ядрах — 69 passed.
  Это два наблюдения, а не доказательство отсутствия: до правки падал 1 прогон из 3, после —
  0 из 2.

Проверено: `invariants.md:3` — версия v11, L2-07 → реализован; `:35` — сам
инвариант «ответ не старее `R<domain>`, свежесть = совпадение ревизий». Окно
рассогласования зафиксировано там, где решение и обязаны его держать:
`05_adr_log.md:1073` («задержка ≤ интервал поллинга») и `design.md:63`
(«окно ≤ интервал поллинга + время обработки запроса»); в строке инварианта
оно выражено через «умирают по TTL» и совпадение ревизий, без числа.
Нумерация: `prototype_requirements.md:287` (bounded staleness → L2-07) и `:346`
(L2-04 → атомарность COMMIT, разные инварианты), `05_adr_log.md:1069`,
`analitic/revision/data_revision_analytics.md:252`. Статус ADR —
`05_adr_log.md:1033` («Accepted, реализовано»). Чеклист —
`prototype_requirements.md:279-289`: пять `[x]`, открыт только `M4-Eval`, который
принадлежит другому бандлу. История — `history.md:592-625` (Этап 12, статус
«Реализовано»).

### Что осталось за пределами отметок

- `proposal.md:31` и `specs/data-revision-query-context/spec.md:76` этого же бандла
  называют bounded staleness «L2-04» — устаревший номер, тот же, что SC-13. Задача
  про нумерацию перечисляет только `prototype_requirements.md`, ADR-026 и аналитику
  (все три верны), а review-запись бандла предписывает `proposal.md`/`design.md`/
  `spec.md` не трогать. Поэтому расхождение записано здесь, а не исправлено молча:
  правка — отдельное решение, и она должна идти в запись бандла, а не в обход неё.
- Гард `ProjectionState.is_ready` (`projection.py:102-119` удаляет `data_revision` из
  состояния) в этот бандл не входит: ни одной задачи про него здесь нет, и
  `test_commit_stage.py:453` упоминает ADR-046 п.8 в докстринге, не вызывая
  `is_ready`. Это отдельная общая дырка, а не недоделка этого бандла.