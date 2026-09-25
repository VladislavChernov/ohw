# Задачи: эксперимент «вклад графа»

> **Supersession notice (2026-09-25):** typed-ontology/constraint tasks 1.1–1.6 and 8.1–8.4
> are historical implementation evidence, not acceptance criteria for the current concept.
> The active corrective plan is `add-lightweight-context-graph`; do not extend the typed model.

> Стадии: типизированный граф (1, предусловие) → формат и журнал (2–5) → прогон
> эксперимента (6) → приёмка (7) → DoD ингеста (8) и судья (9) — обе главы
> являются предусловиями полного прогона 7.2.
> Решение: доработка ингеста и калибровка судьи входят в этот же бандл
> (Вариант А; отдельный бандл не создаётся — см. `analitic/eval/gold_set_proposals.md`).
> Эталонные точки кода: `pipeline.py:163/185`, `run_eval.py:314/359/430-454`,
> `metrics.py:17/55/115`, `orchestrator.py:151/417/442`, `retrievers.py:82-90`,
> формат v2 — `pilots/docs-review/questions.jsonl`.

## 1. Типизированный граф (предусловие эксперимента)

> Текущая загрузка пишет однородные `Entity` без рёбер (`orchestrator.py:442,470-472`),
> а Cypher графовой оси требует `Requirement|Concept|Contract` из онтологии
> (`retrievers.py:82-90`) — соседи не матчатся никак, ось вырождена в простое
> сопоставление узлов. Без типизированного графа вклад оси не измерим.

- [x] 1.1. `ExtractStage`: перенос онтологии в извлечение — боевой LLM-путь по
      `extraction.prompt_template` профиля (`requirements/concepts/contracts` + связи)
      под env-флагом; детерминированный fallback остаётся для fake/inmemory-режима
      (не ломает `EVAL_LLM_ADAPTER=fake` и существующие тесты). Результат содержит
      типизированные `ctx.entities` и `ctx.entity_edges`; endpoints связей нормализуются
      вместе с сущностями и проверяются по `ontology.edge_types`.
      > Проверено 2026-09-24: LLM/fallback extraction, typed payload, profile wiring и
      > `test_typed_graph.py` проходят в dev-контейнере; полный pytest — 433 passed.
- [x] 1.1a. Runtime wiring и профиль джобы: `Executor`/`build_analyzer` передают
      LLM и `profile_fetcher`; профиль загружается один раз и сохраняется в
      `PipelineContext.profile`; `ChunkStage` использует профильный chunker,
      `ExtractStage` и `CommitStage` — тот же снимок. `create_app()` и in-memory
      analyzer собираются с внедрёнными fake-зависимостями без `TypeError`.
- [x] 1.1b. Fallback-политика: при `EXTRACT_LLM=false` и fake/inmemory используется
      deterministic extraction с меткой `Entity`; при явно включённом LLM-пути ошибки
      профиля/LLM не маскируются молчаливым переходом на fallback. Контракт проверяется
      отдельными unit-тестами.
- [x] 1.2. `CommitStage._write`: метка узла — тип извлечённой сущности
      (`Requirement|Concept|Contract`), а не константа `Entity`; рёбра по `edge_types`
      (REQUIRES_CONSTRAINT / CONTRADICTS / SIMILAR_TO / REFERENCES); `Source`/`Chunk`
      и рёбра `Source-CONTAINS-Chunk` без изменений. Для entity↔chunk создаётся
      `Chunk-[:MENTIONS]-> Entity`, а Entity не получает копию текста chunk.
      > Проверено 2026-09-24: typed labels, `MENTIONS`, raw-text exclusion и
      > no-op regression покрыты `test_typed_graph.py`; полный pytest — 433 passed.
- [x] 1.3. Тесты: на онтологии `it` граф содержит типизированные узлы и рёбра;
      скелет ретривера непуст для графо-терминов; проверяются `create_app()`/wiring,
      LLM и fallback-пути, `MENTIONS`, отсутствие raw-текста в Entity; регрессия
      `Source-CONTAINS-Chunk` и no-op повтор (L2-06) сохраняются. `test_typed_graph.py`
      должен быть приведён к согласованному API, включая двухаргументный
      `_entity_node_id(domain, canonical)`, и не должен зависеть от несуществующих полей.
      > Проверено 2026-09-24: typed/live-подготовка unit-контур и полный pytest — 433 passed;
      > live Neo4j acceptance остаётся отдельной задачей 1.4.
- [ ] 1.4. Live: переингст корпуса на Neo4j-стек → в графе есть `:Requirement`,
      `:Concept`, `:Contract`; предупреждение `label does not exist` из логов
      ретривера исчезает.
- [x] 1.5. Schema-провижининг: `GraphStoreProvider.ensure_schema(node_types)` —
      `CREATE CONSTRAINT ... IS UNIQUE` по паре `(domain, unique_key)` каждого типа из онтологии
      (для текущего `it` это `canonical_name` у `Requirement|Concept|Contract`);
      вызов выполняется перед первым COMMIT домена из `CommitStage`, повторные вызовы
      идемпотентны. Config Service не является точкой DDL, поскольку не имеет graph
      adapter/credentials. InMemory — декларативный no-op. Fallback `Entity`-метка
      не требует constraint.
      > Проверено 2026-09-24: `CommitStage` вызывает `ensure_schema()` с
      > `node_types` профиля перед записью; DDL-тесты проходят, live `SHOW CONSTRAINTS`
      > остаётся в задаче 1.4/1.6.
- [ ] 1.6. Тесты schema-провижининга: повторный вызов идемпотентен; уникальность
      по `canonical_name` обеспечивается для всех трёх доменных типов (вставка дубля
      canonical_name падает); live: `SHOW CONSTRAINTS` в Neo4j содержит constraint на
      каждый тип онтологии. Unit-тест должен проверять фактический вызов из
      `CommitStage`, идемпотентность DDL и пропуск fallback-типов; зелёный прогон
      выполняется в dev-контейнере, историческое число passed не является доказательством.

- [ ] 1.7. Контракт изоляции и metadata осей: `chunk_id` включает `domain`;
      graph/vector retrieval ограничивается активным доменом; результат Neo4j vector
      search содержит нормализованные `chunk_id`, `text`, `source_url`, `domain`, `index`.
      Unit/live-тест не допускает коллизии одинакового URL в разных доменах и потери
      текста/metadata при чтении из Neo4j.

## 2. Разметка источников по осям

- [x] 2.1. `pipeline.py: build_sources` принимает `skeleton_rows`; `sources` получают
      признак `axis ∈ {graph, vector}`; графовый скелет включается в `done.sources`
      (закрывает ED10-04). Дедупликация по `(source_url, axis)`.
- [x] 2.2. `done.sources` — аддитивное поле `axis` (контракт ADR-016 не ломается);
      `retrieval_metrics` продолжает работать по старым полям (обратная совместимость).
- [x] 2.3. Unit: axis-поля, дедупликация по парам, отсутствие регрессии `retrieval_metrics`
      (`tests/test_retrieval_core.py::test_build_sources_axes_and_deduplication_by_pair`;
      старые метрики-тесты без изменений, полный pytest зелёный — 384 passed).

## 3. Метрики вклада графа

- [x] 3.1. `metrics.py: graph_contribution(...)` — recall по осям, `evidence_recall_graph`,
      `necessity`, `delta_recall` (вычислительная, без judge). См. `design.md §3`.
      Реализована в `src/graphrag_proto/eval/metrics.py::graph_contribution`
      (`mode=measured/not_measured`, necessity на срезе `golden_graph_evidence`).
- [x] 3.2. Unit на синтетике: вектор-only перекрывает → вклад 0; `graph_required` без
      графовой оси → `necessity > 0`; смешение осей корректно дедуплицируется
      (`tests/test_eval_metrics.py` — 4 новых теста, зелёные).

## 4. Формат вопроса v2 и переработка наборов

- [x] 4.1. `docs/05_adr_log.md`: ADR-015 delta — опциональные поля v2 (§4 design.md),
      `golden_graph_evidence`, атомарность facts. Валидация наборов расширяется.
      Реализовано: delta v2-полей в ADR-015.
- [x] 4.2. `run_eval.load_dataset` + `tests/test_eval_dataset.py`: v2-поля валидируются
      (типы, `evidence_policy=graph_required ⇒ golden_graph_evidence`), старые наборы
      остаются парсибельными. Реализовано: `validate_question`/`load_dataset`; набор
      `test_eval_dataset.py` зелёный.
- [x] 4.3. Переразметка `it/questions.jsonl` (50) в формат v2: атомарные facts,
      `reasoning_type`, `answerability`, `as_of`, `evidence_sections`, `evidence_policy`,
      `rubric` (по образцу пилота; категории сохранить). Реализовано (50 вопросов v2,
      `as_of 2026-09-22`).
- [x] 4.4. Новый `it/questions_graph.jsonl`: графо-специфичные/multi-hop,
      `golden_graph_evidence`, цель ≥ 15 вопросов с ≥ 8 `graph_required`. Реализовано:
      16 вопросов, 9 `graph_required` с `golden_graph_evidence: true`; факты сверены с
      `docs/*.md`; `test_it_questions_graph_dataset` зелёный.
- [x] 4.5. Раннер: `--extra-dataset` (несколько наборов мержатся, id сохраняются).
      Реализовано: `merge_datasets` + флаг; тест на дедупликацию id зелёный.

## 5. Артефакты прогона и режимы запуска

- [x] 5.1. `run_eval.py`: `qa_log.jsonl` (слой 2, схема design.md §5.2) — append на вопрос;
      переживает прерывание; включает per-вопросные retrieval + timings.
      Реализовано: `_append_jsonl`/`eval_question` (retrieved с axis, graph_contribution,
      timings, generation.mode); файл обнуляется в начале прогона, пишется по вопросу.
- [x] 5.2. `--no-judge` (`EVAL_LLM_ADAPTER=none`): генерация есть, judge нет,
      `generation.mode="n/a"`; контуры — как сейчас.
      Реализовано: `build_judge` → None для none/fake/empty; `generation.mode="n/a"`.
- [x] 5.3. `--retrieval-only`: без генерации (экономия LLM-времени на fast-loop).
      Реализовано: `pipeline.run(generate=False)` (text="", query LLM не вызывается),
      `generation.mode="skipped"`; preflight исключает LLM генерации, но сохраняет его
      требование для `--corpus` при `EXTRACT_LLM=true`.
- [x] 5.4. `lift_report`: блок `graph_contribution`; `verdict` = `n/a` при отсутствии judge.
      Реализовано: `lift_report(judge_active=...)` → verdict "n/a"; дельты
      necessity/delta_recall/evidence_recall_graph; агрегат в `aggregate`.
- [x] 5.5. Unit: фейк-pipeline, проверка содержимого qa_log, режимы 5.2/5.3 (судья не
      вызывается, ответы не генерируются в 5.3).
      Реализовано: `tests/test_run_eval_artifacts.py` — modes n/a/skipped, recall,
      graph_contribution measured.
- [x] 5.6. `run_eval.py`: `run_manifest.json` (слой 1, схема design.md §5.1) — один раз в
      начале прогона: `run_id`, `started_at`, `code_commit`, `domain`, `revision` +
      `revision_fingerprint`, `datasets`, `k`, `mode`, `graph_enabled`, `components`
      (embedder/dimensions/reranker/chunker), `generation` (llm/judge adapter, temperature),
      `flags`.
      Реализовано: `build_run_manifest`/`write_run_manifest`; `code_commit` из
      env/`git rev-parse` c fallback "unknown".
- [x] 5.7. `run_eval.py --trace`: `trace.jsonl` (слой 4, append на вопрос, только по флагу) —
      события pipeline (`embedding`/`cache`/`graph`/`vector`/`rerank`/`llm`/`done`), скелет
      графа, состав контекста и факт вытеснения чанков по лимиту токенов, скоры до/после
      реранкера. Без флага файл не создаётся; слои 1–3 не подменяются и метрики не зависят
      от наличия трассы.
      Реализовано: `pipeline.run(trace=...)` формирует `done["trace"]`; `run_eval` пишет
      trace.jsonl только при `--trace`; события из `done["trace"]`.
- [x] 5.8. Unit: манифест пишется один раз и фиксирует факторы; `trace.jsonl` появляется
      только при `--trace`, отсутствует в обычном режиме; слои 1–3 присутствуют в
      `--no-judge` и `--retrieval-only`.
      Реализовано: `test_run_eval_artifacts.py` (manifest, trace только по флагу,
      n/a/skipped, сравнение манифестов).
- [x] 5.9. `lift_report.md`: блок «Условия прогона» из манифеста + diff факторов парных
      прогонов; расхождение больше чем в одном поле фактора → предупреждение «прогоны
      не парные» (правило design.md §5.1), вердикт помечается как недействительный.
      Реализовано: `write_lift_report(report, out, manifest)`; `compare_run_manifests`
      (факторы mode/graph_enabled), `report["pair"]` + verdict "invalid"; флаг
      `--compare-with <файл|дир>`.

## 6. Документация

- [x] 6.1. `docs/05_adr_log.md`: ADR-029 (Draft) — методика эксперимента «вклад графа»
      (парные прогоны, критерий необходимости, запрет интерпретации без осей,
      judge-less как fast-loop).
      Реализовано: ADR-029 (Draft, 2026-09-22): парные прогоны на одной ревизии и
      правило парности (факторы mode/graph_enabled, `--compare-with`), критерий
      необходимости (срез `golden_graph_evidence`, necessity/delta_recall/per-axis
      recall), запрет интерпретации без осей (`axis` в done.sources), judge-less как
      fast-loop (слои 1–3 обязательны всегда, verdict n/a, слой 4 по требованию);
      заголовок журнала → v5.5 (ADR-001 — ADR-029).
- [x] 6.2. `docs/invariants.md`: инвариант «оценка не зависит от железа судьи»
      (артефакты обязательны и без judge).
      Реализовано: L5-05 (v11): слои 1–3 пишутся во всех режимах (включая
      --no-judge/--retrieval-only), наличие/полнота не зависят от судьи; без судьи
      groundedness/coverage = n/a и не участвуют в вердикте; trace (слой 4) — по
      требованию.
- [x] 6.3. `docs/history.md`: Этап 15 — эксперимент «вклад графа».
      Реализовано: Этап 15 (находка/стадии 1–6, верификация pytest 420 passed,
      следующие шаги — стадия 7); таблица «Связи» обновлена на ADR-001 — ADR-029.
- [x] 6.4. `prototype/infra/eval/README-minimal.md`: снять «исправить раннер и методику
      согласно ревью №09/10» (54), обновить команды, режимы запуска и перечень артефактов
      (`run_manifest.json`, `qa_log.jsonl`, `lift_report.*`, `trace.jsonl` + `--trace`).
      Реализовано: раздел «Парный прогон и режимы» (`--mode both` вместо двух
      раздельных прогонов, UC12-01 исправлен), таблица режимов
      (default/--no-judge/--retrieval-only), таблица послойных артефактов (ADR-029 /
      L5-05), `--compare-with`; снят пункт про «исправить согласно ревью №09/10».
- [x] 6.5. Сверка списка слоёв артефактов между `design.md` §5, `docs/test_plan.md` §5 и
      `learning/prototype_test_plan_learning.md` §5 — единый состав и имена файлов.
      Реализовано: во всех трёх документах 4 слоя с едиными именами
      `<out>/run_manifest.json`, `<out>/qa_log.jsonl`, `<out>/lift_report.json`+`.md`,
      `<out>/trace.jsonl`; §5.3 test_plan обновлён (трасса сохраняется раннером при
      `--trace`, диагностика не подменяет слои 1–3); learning §6.3 — дефект `both`
      переформулирован как исправленный (UC12-01).

- [ ] 6.6. Синхронизировать SSOT-документацию с принятым контрактом после зелёного
      кода: `docs/01_ontology_and_domain_profile.md`, `docs/data_model.md`,
      `docs/adapters_specification.md`, `docs/invariants.md` и связанные ADR — единый
      `canonical_name`, `Chunk-[:MENTIONS]-> Entity`, `chunk_id` с `domain`, вызов
      `ensure_schema` перед первым COMMIT. До подтверждения кодом старые утверждения
      об активации профиля и `id`-unique_key не считать актуальными.

## 7. Приёмка и прогон эксперимента

- [ ] 7.1. `uv run pytest -q` зелёно; `uv run ruff check .`, `uv run mypy src` — чисто.
      Отдельно подтверждаются startup/wiring ingestion, LLM/fallback-пути, typed graph,
      `MENTIONS`, `ensure_schema` и идемпотентный no-op повтор.
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

## 8. DoD по ингесту (Вариант А)

> Утверждено: доработка ингеста идёт внутри этого бандла (Вариант А), отдельный
> бандл не создаётся. DoD считается закрытым, когда выполнены **все** пункты
> 8.1–8.4. Предусловие для live-переингеста 1.4 и полного прогона 7.2.

- [x] 8.1. Единый `_identity_key`: канонический ключ узла сущности — `canonical_name`
      для всех трёх типов онтологии (`Requirement`/`Concept`/`Contract`), нормализованный
      через `NFKC`/`ё → е`/`casefold`/сжатие пробелов, без type-суффикса в node_id
      (сейчас `ent:{domain}:{type}:{canonical}` → `ent:{domain}:{canonical}`); роль типа
      остаётся в **метке** узла, совпадение ключа у разных типов объединяет метки
       одного узла. Уникальность по паре `(domain, unique_key)` обеспечивает constraint из 1.5.
- [x] 8.2. Вырез эхо-текста: в свойствах узла сущности нет дубля текста чанка или raw-цитат
      (поля `text`/копии исходного текста отсутствуют); ссылка на содержимое — только
      через `chunk_ids`/`source_ids`. Один текст хранится один раз.
- [x] 8.3. Связка сущности с `chunk_id`: сущность жёстко привязана к чанку-источнику
      свойством `chunk_ids` и ребром `Chunk-[:MENTIONS]-> Entity`; направление фиксировано,
      чтобы графовая и векторная оси резолвились к одному и тому же контенту по общему
      ключу (data_model.md §6) — по нему же сверяются `golden_graph_evidence`. В `chunk_id`
      включается `domain`, чтобы одинаковый `source_url` в разных доменах не пересекался.
- [ ] 8.4. Тесты + live: unit-тесты `_identity_key` (дедуп: сущность одного имени,
      встреченная в разных чанках/типах, склеивается в один узел; эхо-текста и raw-цитат
      в properties нет), проверяются wiring `create_app`, LLM/fallback-пути и
       `MENTIONS`; регрессия L2-06 (no-op повтор) сохраняется для неизменного
       content/pipeline-контракта; live-переингст —
       дубликаты канонических узлов не появляются, в trace/qa_log видна связка
      `chunk_id` ↔ сущность. Полный pytest + ruff + mypy зелёные.

## 9. Судья: ревизия набора, версия промпта, калибровка

> Судья — прибор с собственным дрейфом (`analitic/eval/judge_calibration_analysis.md`).
> Все три задачи — предусловия полного прогона с судьёй (7.2); в judge-less
> режимах (2.x–5.x) не требуются.

- [ ] 9.1. Ревизия 50 вопросов в `it/questions.jsonl`: **до** переразметки в формат
      v2 (4.3) набор настраивается — «фонящие» вопросы (расплывчатый эталон,
      вопросы-ловушки, устаревшие относительно ревизии) переписываются или
      удаляются; фиксируется итоговый состав (n) и список изменений в отчёте.
      Статус набора — «контрольные вопросы», **не gold set** (human-оценок нет):
      это отражается комментарием в датасете и в отчёте.
- [ ] 9.2. Фиксация версии промпта судьи: judge-prompt и модель судьи
      версионируются и записываются в `run_manifest.json` (слой 1); смена версии =
      новый «прибор», вердикты разных версий не сравниваются напрямую — дрейф
      оценки атрибутируем.
- [ ] 9.3. Разовая калибровка судьи: до 7.2 — на выборке ≥ 10 пар вопросов
      ручная оценка параллельно судье, фиксируется доля согласия и список
      расхождений; систематическое расхождение = правка промпта/критериев и
      повтор до прогона; результат калибровки — в отчёте эксперимента.

## 10. M6-Growth / pre-connectors — lifecycle проекций (отложено)

> Статус: **отложено после текущего эксперимента**, до подключения внешних
> коннекторов. Не является предусловием задач 7.1–7.2 и не блокирует прототип.
> До этой стадии профиль/ontology и pipeline-контракт загруженного корпуса считаются
> неизменными; автоматический reindex после смены профиля не поддерживается.

- [ ] 10.1. Projection fingerprint/state: разделить `content_hash`, `vector_fingerprint`
      (chunking/embedding) и `graph_fingerprint` (profile/ontology/extraction); описать
      состояние `current/building/failed` и правила no-op отдельно для каждой проекции.
- [ ] 10.2. Transactional outbox: атомарно фиксировать событие смены содержимого/профиля
      рядом с registry; отдельные идемпотентные workers обновляют graph и vector, поддерживают
      retry, backoff, dead-letter и наблюдаемость без 2PC.
- [ ] 10.3. Dual-generation migration: строить новую graph/vector generation рядом со старой,
      валидировать её, публиковать active manifest/pointer только после успеха, затем
      безопасно освобождать старые generation; запрос фиксирует одну revision.
- [ ] 10.4. Reindex/cleanup: обработка смены профиля, онтологии, chunker, embedding и
      graph schema; vector/graph backend остаются за `GraphStoreProvider`/`VectorStoreProvider`,
      без привязки к Neo4j. Для текущего M4-эксперимента сохраняется парность baseline/target.
- [ ] 10.5. Acceptance: unit + offline integration на fake/S3-compatible/двух adapter’ах,
      проверка partial failure, повторного запуска, отсутствия смешивания generations и
      восстановления после потери worker; live-нагрузка не входит в DoD прототипа.