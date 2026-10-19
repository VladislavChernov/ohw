# Задачи

Задача считается закрытой, когда подтверждена гейтом, а не когда выглядит сделанной.

Scope прототипа: ревизия перестаёт быть предусловием запуска, гард парности начинает мерить
(а не сравнивать эхо с эхом), отчёт отличает «неизвестно» от нуля, манифест называет границы
прогона, появляется runner для демо-контура. Состав документов из реестра — отложенная стадия
(триггер в `spec.md`, в задачи не входит).

Решение о ревизии было опровергнуто кодом и поправлено в `design.md` до реализации: ревизия
нужна, чтобы открыть графическую ось (`pipeline.py:506`), и в ответе её нет — `done.revision`
это эхо переданного (`pipeline.py:842`).

## Ревизия: обязательна для графа, не обязательна для старта

- [x] `--revision auto|none` (по умолчанию `auto`): чтение `GET /api/v1/ingestion/revision`
      перестаёт быть жёстким требованием — отказ даёт `revision=None`, а не падение прогона
- [x] `revision_source` в манифест: `reported` / `unavailable` / `skipped`; `revision: null`
      вместо подставленного значения; `--revision=none` попадает в `flags`
- [x] Preflight требует ingestion-api только при `--corpus` (параметр `include_ingestion` уже
      передаётся вызывающим кодом и игнорируется самим списком контуров)
- [x] Тесты: контур недоступен — прогон идёт и пишет `unavailable`; `--revision none` пишет
      `skipped`; успешное чтение даёт `reported`

## Гард парности

- [x] Убрать заглушку: сравнение `done["revision"]` с переданной величиной не может сработать
      (эхо эха) — вместе с веткой `main`, которая ловит эту строку
- [x] `projection_revision` из ответов: первое наблюдение — база, расхождение — стоп с
      именованной причиной
- [x] Повторное чтение доменной ревизии в конце прогона: `stable` / `changed` (вердикт
      `invalid` с причиной в отчёте, манифесте и паспорте) / `unavailable` (гард не исполнился)
- [x] Тесты: смена `projection_revision` роняет прогон; расхождение ревизии даёт `invalid`;
      непрочитанная ревизия даёт `unavailable`, а не `stable`

## Состав корпуса не молчит нулём

- [x] `golden_coverage`: без загрузок в прогоне и без источника состава — `mode="unknown"` с
      причиной и счётчиками `null`; `questions_total` заполняется
- [x] Режим `--corpus` считает по-прежнему против загруженного состава, `mode="measured"`
- [x] Паспорт: в режиме `unknown` — строка «покрытие не измерено», а не предупреждение о низком
- [x] Тесты: неизвестно ≠ ноль; загрузочный режим не изменился

## Манифест

- [x] `finished_at` в манифест и в паспорт (`started_at` уже пишется); блок `parity`
- [x] Тест: манифест несёт обе метки времени, `revision_source` и `parity`

## Runner демо-контура

- [x] `compose.eval-runtime.yaml`: сервис `eval-runner` (профиль `eval`) в сети `runtime_net`,
      с `../src`, `../infra/eval`, томом `projection` и каталогом отчёта на хосте
- [x] Env runner'а совпадает с `query-worker` по контуру запросов (`CONFIG_URL`, `NEO4J_*`,
      `EMBEDDER`, `EMBEDDINGS_URL`, `LLM_*`, `GRAPH_STORE`, `VECTOR_STORE`, `AUTH_API_KEY`,
      `PROJECTION_STATE_DB_PATH`, `PROJECTION_CONFIG_FINGERPRINT`) плюс `INGESTION_URL` и
      `RUN_CODE_COMMIT`
- [x] Гард: без общего тома `projection` runner даёт `projection_status=missing` в `qa_log` —
      остаётся видимым в отчёте, а не тихим vector-only
      (`tests/test_demo_scripts_parity.py`, общий том и отпечаток проверяются на файлах)

## Приёмка

- [x] `gate.sh full` зелёный (2026-10-16: 1176 passed, 5 skipped)
- [ ] Ручной прогон среза `Demo/eval-slice.jsonl` на демо-стенде (`up --set ready --with-ingest` +
      `eval-runner`): манифест с `finished_at`, `revision_source` и `parity`, `golden_coverage`
      в режиме «неизвестно», а `necessity`/`delta_recall`/`timings` — измеренными
- [ ] После этого закрывается и `eval-ready-slice` (его последний шаг — этот прогон)

### Что показал прогон 2026-10-16 (замер, а не мнение)

Прогон выполнен дважды, артефакты: `prototype/reports/slice-2026-10-16/` и `slice-2026-10-16b/`.

| проверенное требование | результат |
|---|---|
| `revision_source`, `parity` | `reported`, `parity.revision=stable` |
| `golden_coverage` не молчит нулём | `mode=unknown`, `reason=corpus_composition_unavailable`, `questions_total=12`, счётчики `null` |
| `finished_at` в манифесте | есть, совпадает с `run_state.finished_at` |
| `necessity`/`delta_recall` | **не измерены**: `mode=not_measured`, `degraded_questions=12` |

Первый прогон вскрыл дефект оверлея: в `eval-runner` был объявлен только `AUTH_API_KEY`, а
`run_eval.py:51` читает `X_API_KEY`/`GRAPH_AUTH_API_KEY`. Оба пусты → заголовок уходил с
`changeme`, ingestion-api отвечал 401 → `revision_source=unavailable`,
`projection_status=revision_unknown`, и обе рукава молча мерили vector. Исправлено и закрыто
гардом `test_eval_runner_authenticates_with_the_same_key_as_the_contours` (проверено: с
удалённой строкой гард падает).

**Причина, по которой вклад графа не измерен — состояние корпуса, а не прибор.** Готовность
пораздельная (ADR-046 п.3), прибором `run_live_gate_check`-логики на живом стенде:

| величина | значение |
|---|---|
| `sources_in_journal` | 30 |
| `sources_in_graph` | 17 |
| `sources_without_graph` | **13** |
| `revision_mismatches` / `chunks_without_owner` | 0 / 0 |
| `projection_status` в qa_log | `sources_without_graph` (12/12 вопросов target) |

Ноль `sources_without_graph` — условие готовности, поэтому ось выключена на всём прогоне. Том
`ready` наполнен на 17 документов из 30 (`README-deployment.md` §3, работа по догрузке —
`openspec/changes/fill-ready-corpus/`). Значит последний пункт приёмки упирается в состав
корпуса, а не в код этого бандла: `necessity`/`delta_recall` станут измеримыми на корпусе без
источников «без графа».

Побочный измеренный факт: vector-only на этом срезе работает — recall@5 = 0.6389 в обеих
рукавах, 12 вопросов ≈ 60 с на рукав (retrieval 30 с, генерация 28 с).