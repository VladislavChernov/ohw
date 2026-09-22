# Задачи: эксперимент «вклад графа»

> Стадии: типизированный граф (1, предусловие) → формат и журнал (2–5) → прогон
> эксперимента (6) → приёмка (7).
> Эталонные точки кода: `pipeline.py:163/185`, `run_eval.py:314/359/430-454`,
> `metrics.py:17/55/115`, `orchestrator.py:151/417/442`, `retrievers.py:82-90`,
> формат v2 — `pilots/docs-review/questions.jsonl`.

## 1. Типизированный граф (предусловие эксперимента)

> Текущая загрузка пишет однородные `Entity` без рёбер (`orchestrator.py:442,470-472`),
> а Cypher графовой оси требует `Requirement|Concept|Contract` из онтологии
> (`retrievers.py:82-90`) — соседи не матчатся никак, ось вырождена в простое
> сопоставление узлов. Без типизированного графа вклад оси не измерим.

- [ ] 1.1. `ExtractStage`: перенос онтологии в извлечение — боевой LLM-путь по
      `extraction.prompt_template` профиля (`requirements/concepts/contracts` + связи)
      под env-флагом; детерминированный fallback остаётся для fake/inmemory-режима
      (не ломает `EVAL_LLM_ADAPTER=fake` и существующие тесты).
- [ ] 1.2. `CommitStage._write`: метка узла — тип извлечённой сущности
      (`Requirement|Concept|Contract`), а не константа `Entity`; рёбра по `edge_types`
      (REQUIRES_CONSTRAINT / CONTRADICTS / SIMILAR_TO / REFERENCES); `Source`/`Chunk`
      и рёбра `Source-CONTAINS-Chunk` без изменений.
- [ ] 1.3. Тесты: на онтологии `it` граф содержит типизированные узлы и рёбра;
      скелет ретривера непуст для графо-терминов; регрессия `Source-CONTAINS-Chunk`
      и no-op повтор (L2-06) сохраняются.
- [ ] 1.4. Live: переингст корпуса на Neo4j-стек → в графе есть `:Requirement`,
      `:Concept`, `:Contract`; предупреждение `label does not exist` из логов
      ретривера исчезает.
- [ ] 1.5. Schema-провижининг: `GraphStoreProvider.ensure_schema(node_types)` —
      `CREATE CONSTRAINT ... IS UNIQUE` по `unique_key` каждого типа из онтологии
      (идемпотентно); вызов при активации профиля и/или при первом COMMIT домена
      (точку зафиксировать по тесту 1.6; доки `docs/01 §3` обещают «config-service
      при активации» — проверить, что у config-service нет доступа к графу, и решить
      размещение по фактам). InMemory — декларативный no-op. Fallback `Entity`-метка
      не требует constraint.
- [ ] 1.6. Тесты schema-провижининга: повторный вызов идемпотентен; уникальность
      по `unique_key` обеспечивается (вставка дубля canonical_name падает);
      live: `SHOW CONSTRAINTS` в Neo4j содержит constraint на каждый тип онтологии.

## 2. Разметка источников по осям

- [ ] 2.1. `pipeline.py: build_sources` принимает `skeleton_rows`; `sources` получают
      признак `axis ∈ {graph, vector}`; графовый скелет включается в `done.sources`
      (закрывает ED10-04). Дедупликация по `(source_url, axis)`.
- [ ] 2.2. `done.sources` — аддитивное поле `axis` (контракт ADR-016 не ломается);
      `retrieval_metrics` продолжает работать по старым полям (обратная совместимость).
- [ ] 2.3. Unit: axis-поля, дедупликация по парам, отсутствие регрессии `retrieval_metrics`.

## 3. Метрики вклада графа

- [ ] 3.1. `metrics.py: graph_contribution(...)` — recall по осям, `evidence_recall_graph`,
      `necessity`, `delta_recall` (вычислительная, без judge). См. `design.md §3`.
- [ ] 3.2. Unit на синтетике: вектор-only перекрывает → вклад 0; `graph_required` без
      графовой оси → `necessity > 0`; смешение осей корректно дедуплицируется.

## 4. Формат вопроса v2 и переработка наборов

- [ ] 4.1. `docs/05_adr_log.md`: ADR-015 delta — опциональные поля v2 (§4 design.md),
      `golden_graph_evidence`, атомарность facts. Валидация наборов расширяется.
- [ ] 4.2. `run_eval.load_dataset` + `tests/test_eval_dataset.py`: v2-поля валидируются
      (типы, `evidence_policy=graph_required ⇒ golden_graph_evidence`), старые наборы
      остаются парсибельными.
- [ ] 4.3. Переразметка `it/questions.jsonl` (50) в формат v2: атомарные facts,
      `reasoning_type`, `answerability`, `as_of`, `evidence_sections`, `evidence_policy`,
      `rubric` (по образцу пилота; категории сохранить).
- [ ] 4.4. Новый `it/questions_graph.jsonl`: графо-специфичные/multi-hop,
      `golden_graph_evidence`, цель ≥ 15 вопросов с ≥ 8 `graph_required`.
- [ ] 4.5. Раннер: `--extra-dataset` (несколько наборов мержатся, id сохраняются).

## 5. Артефакты прогона и режимы запуска

- [ ] 5.1. `run_eval.py`: `qa_log.jsonl` (слой 2, схема design.md §5.2) — append на вопрос;
      переживает прерывание; включает per-вопросные retrieval + timings.
- [ ] 5.2. `--no-judge` (`EVAL_LLM_ADAPTER=none`): генерация есть, judge нет,
      `generation.mode="n/a"`; контуры — как сейчас.
- [ ] 5.3. `--retrieval-only`: без генерации (экономия LLM-времени на fast-loop).
- [ ] 5.4. `lift_report`: блок `graph_contribution`; `verdict` = `n/a` при отсутствии judge.
- [ ] 5.5. Unit: фейк-pipeline, проверка содержимого qa_log, режимы 5.2/5.3 (судья не
      вызывается, ответы не генерируются в 5.3).
- [ ] 5.6. `run_eval.py`: `run_manifest.json` (слой 1, схема design.md §5.1) — один раз в
      начале прогона: `run_id`, `started_at`, `code_commit`, `domain`, `revision` +
      `revision_fingerprint`, `datasets`, `k`, `mode`, `graph_enabled`, `components`
      (embedder/dimensions/reranker/chunker), `generation` (llm/judge adapter, temperature),
      `flags`.
- [ ] 5.7. `run_eval.py --trace`: `trace.jsonl` (слой 4, append на вопрос, только по флагу) —
      события pipeline (`embedding`/`cache`/`graph`/`vector`/`rerank`/`llm`/`done`), скелет
      графа, состав контекста и факт вытеснения чанков по лимиту токенов, скоры до/после
      реранкера. Без флага файл не создаётся; слои 1–3 не подменяются и метрики не зависят
      от наличия трассы.
- [ ] 5.8. Unit: манифест пишется один раз и фиксирует факторы; `trace.jsonl` появляется
      только при `--trace`, отсутствует в обычном режиме; слои 1–3 присутствуют в
      `--no-judge` и `--retrieval-only`.
- [ ] 5.9. `lift_report.md`: блок «Условия прогона» из манифеста + diff факторов парных
      прогонов; расхождение больше чем в одном поле фактора → предупреждение «прогоны
      не парные» (правило design.md §5.1), вердикт помечается как недействительный.

## 6. Документация

- [ ] 6.1. `docs/05_adr_log.md`: ADR-029 (Draft) — методика эксперимента «вклад графа»
      (парные прогоны, критерий необходимости, запрет интерпретации без осей,
      judge-less как fast-loop).
- [ ] 6.2. `docs/invariants.md`: инвариант «оценка не зависит от железа судьи»
      (артефакты обязательны и без judge).
- [ ] 6.3. `docs/history.md`: Этап 15 — эксперимент «вклад графа».
- [ ] 6.4. `prototype/infra/eval/README-minimal.md`: снять «исправить раннер и методику
      согласно ревью №09/10» (54), обновить команды, режимы запуска и перечень артефактов
      (`run_manifest.json`, `qa_log.jsonl`, `lift_report.*`, `trace.jsonl` + `--trace`).
- [ ] 6.5. Сверка списка слоёв артефактов между `design.md` §5, `docs/test_plan.md` §5 и
      `learning/prototype_test_plan_learning.md` §5 — единый состав и имена файлов.

## 7. Приёмка и прогон эксперимента

- [ ] 7.1. `uv run pytest -q` зелёно; `uv run ruff check .`, `uv run mypy src` — чисто.
- [ ] 7.2. Парный прогон `--mode both` на живом Neo4j-стеке (одна revision; переработанный
      it-набор + графовый поднабор): `run_manifest.json` и `qa_log.jsonl` непусты, манифесты
      веток различаются только полем `graph_enabled`, в отчёте блок `graph_contribution`;
      фиксация результата в reports + history.
- [ ] 7.3. Ручной разбор выборки пар (n ≥ 10) из qa_log — сан-чек согласованности
      метрик с ручной оценкой; расхождения фиксируются в отчёт.
- [ ] 7.4. Хронометраж fast-loop (retrieval-only) vs полный прогон с судьёй — фиксация
      времени в отчёте (ожидание: кратно N вопросов, а не N × judge-вызовы).
- [ ] 7.5. Диагностический прогон с `--trace` на 1–2 вопросах: трасса объясняет состав
      контекста и вытеснение чанков, согласована с записью вопроса в `qa_log.jsonl`
      (трасса не подменяет метрики).