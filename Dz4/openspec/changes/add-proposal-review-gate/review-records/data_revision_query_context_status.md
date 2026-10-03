# Review-record: состояние бандла `data-revision-query-context`

```text
review-record:
  proposal: >
    openspec/changes/data-revision-query-context/ (proposal.md, design.md,
    specs/data-revision-query-context/spec.md, tasks.md) — ревизия данных в
    query-контуре как экспорт существующих версий DocumentRegistry.
  reviewer/date: codex, 2026-10-03
  decision: accepted_as_implemented_pending_checklist
  accepted_scope: >
    Текст решений бандла не переписывается — он исторически точен и на него опирается
    ADR-026. Переносится только состояние чеклиста: 18 задач имеют [ ] при полностью
    реализованном коде, и это делает запись неверной в сторону «не начато».
```

## Почему решение именно такое

Бандл **не закрыт**: ноль отметок за всю историю git (два коммита, `c9a8dec` и `7ec78b8`).
Прецедент `accepted_as_history` из `m1_ingestion_claims.md` запрещает переписывать чеклист
**закрытого** бандла, потому что он и есть история. Здесь ситуация обратная: чеклист —
незаконченная работа, и без отметок он врёт в сторону «ничего не сделано». Поэтому состояние
проставляется, текст решений не трогается.

## Проверено по коду — реализовано

| утверждение предложения | где подтверждено |
|---|---|
| `DocumentRegistry.data_revision(domain)`, идемпотентен на no-op | `prototype/tests/test_ingestion_revision.py` (пустой домен → `None`, значение совпадает с реестром) |
| `GET /api/v1/ingestion/revision?domain=` | тот же тест, строки 97–107; URL используется в `infra/eval/pilots/docs-review/corpus_tools.py:114` |
| `RevisionClient` с интервалом, `revision_poll_errors_total`, fail-open | `prototype/tests/test_revision_client.py` — проверены poll, невалидный ответ, `REVISION_POLL_INTERVAL_S=0` (поллер выключен), инкремент счётчика при пяти сбоях |
| Semantic Cache: `lookup/store` принимают ревизию, epoch-bump | `tests/test_retrieval_pipeline.py:632-658` — `done["revision"]` равен `revA`, `None`, `revB` |
| Redis-ключ `query:sc:<rev>:<domain>` | `prototype/infra/config` и код семантического кэша (2 вхождения `query:sc:`) |
| наблюдаемость: счётчик в снапшоте воркера | `docs/invariants.md:59` (L4-04) — `revision_poll_errors_total` и `revisions` в `trigger_metrics snapshot` |

## Задача, которую теперь можно закрыть

Пункт задач про гард на `data_revision` → `ProjectionState.is_ready` помечен «не подтверждена
кодом». **Подтверждена.** `prototype/src/graphrag_proto/ingestion_service/projection.py:102-119`:
`is_ready` принимает `data_revision`, сразу делает `del` и возвращает результат только по
`status` и `config_fingerprint`. В докстринге сказано почему: отпечаток состава домена не
отвечает на вопрос «изменились ли данные этого источника», поэтому остаётся атрибуцией, а
решение о готовности принимает пораздельная проверка ревизий источников (ADR-046 п. 8).
Это совпадает с формулировкой `docs/02:209` и `docs/03:40`, исправленных в коммите `9774e63`.

## Расхождение по имени теста (мелкое, требует решения)

Пункт 4 «Проверка» предлагает `test_worker_revision.py`. Файла с таким именем нет; ревизия
покрыта `test_revision_client.py` и `test_worker_hotreload.py`. Либо переименовать, либо
исправить ссылку в бандле — иначе проверка указывает на несуществующий файл.

## НЕ проверено в этом проходе

Честное перечисление, чтобы отчёт не читался как полный:

- точные сигнатуры `RevisionClient` против описанных (интервал, `REVISION_TIMEOUT_S`, дефолты);
- идемпотентность `data_revision` именно на no-op INGEST (тест проверяет пустой домен, не no-op);
- код 422 без `domain`;
- ключуется ли InMemory-бакет по `(domain, revision)`;
- статус L2-07 в `invariants.md` — снят ли «planned»;
- отмечен ли ADR-026 как реализованный и закрыт ли чеклист Вехи 4-хвост в `prototype_requirements.md`.

Это шесть проверок, каждая — одна команда. Их стоит закрыть перед проставлением отметок,
иначе чеклист станет правдивым по части задач.

## Что делать

1. Закрыть шесть проверок выше.
2. Проставить `[x]` у фактически выполненных 18 задач, у невыполненных — написать, что именно
   осталось (например, гард на `is_ready` можно закрывать сразу, см. выше).
3. Не трогать `proposal.md`, `design.md` и `spec.md`.

## Связанное, найденное попутно

`docs/prototype_requirements.md:168` объявляет M4 «🚧 в работе» и указывает
`openspec/changes/add-prototype-m4-eval` как источник истины по чеклисту. Это отдельная находка
для следующего бандла в очереди: бандл помечен Superseded, а документ на него ссылается.