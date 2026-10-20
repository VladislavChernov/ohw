# Задачи

Задача считается закрытой, когда подтверждена гейтом, а не когда выглядит сделанной.

## Запись (до кода)

- [x] ADR-026: поправка п.3 — источник ревизии в query-контуре, границы и цена
- [x] `prototype/infra/compose.runtime.yaml`: комментарий контура переписан (ревизия не
      запрашивается у ингеста)
- [x] Спека `data-revision-query-context`: разделы про `RevisionClient` и метрику помечены
      переопределёнными (ссылка на эту спеку)
- [x] `semantic_cache.py`: докстринг не ссылается на снятый `RevisionClient`

## Источник ревизии

- [x] Воркер получает `ProjectionStateStore` тем же путём, что пайплайн
      (`PROJECTION_STATE_DB_PATH`), и читает `data_revision` домена
- [x] Готовность графа по-прежнему считается пораздельно (ADR-046 п.8): доменная ревизия
      не участвует в `is_ready`
- [x] `revision` передаётся в `pipeline.run` и попадает в `done` без изменения формы
- [x] В `done` добавлен `revision_source`: `reported` | `unavailable` (имена — как в
      `run_eval.py:106-108`)
- [x] Ревизия неизвестна: `revision = null`, `revision_source = unavailable`; ответ
      формируется, кэш работает в эпохе-константе
- [x] Эпоха «unknown» — именованная константа, не пересекающаяся с `sha256`-значениями
- [x] `RevisionClient`, его поллер и `revision_poll_errors_total` сняты; вместо них счётчик
      ошибок чтения проекции; `known_revisions()` снят или переименован
- [x] `GET /api/v1/ingestion/revision` не тронут

## Гарды

- [x] Строка есть: `revision == state.data_revision`, `revision_source == reported`
- [x] Строки нет: `revision is None`, `revision_source == unavailable`, ответ не падает
- [x] Отсутствие ревизии не превращается в `""`/`0` (проверка типом и значением)
- [x] Счётчик ошибок чтения растёт на сбое чтения и не растёт на отсутствии строки
- [x] Гард, что воркер не делает HTTP-запросов к ингесту (нет `RevisionClient` в коде
      воркера; поведенческая проверка подменой транспорта, если она дешевле)
- [x] Сценарий: `empty`-набор даёт `projection_status=missing`, а не `revision_unknown`

## Проверка

- [x] `pytest prototype/tests` (все)
- [ ] `run_eval --mode both --no-judge` на `ready`: `revision_source = reported`,
      `revision` совпадает с эпохой `projection_revision`
- [ ] `run_eval` на `empty`: `revision = null`, `revision_source = unavailable`, граф выключен
      по `projection_status`
- [x] `gate.sh full`

Два непроверенных пункта требуют живого стенда eval; в этой сессии стенд не поднимался.
Код и гарды проверены `pytest` и `gate.sh full`; живой прогон остаётся отдельным шагом и
здесь не выдан за выполненный.
