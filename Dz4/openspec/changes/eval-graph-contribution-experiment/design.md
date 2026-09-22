# Design: эксперимент «вклад графа»

## 0. Предусловие: типизированный граф

Разбор логов M5 выявил: Cypher-шаблон графовой оси (`retrievers.py:82-90` подставляет
метки из `ontology.node_types`) требует соседей `m:Requirement|Concept|Contract`, но
загрузка пишет только `Source|Entity|Chunk` (`orchestrator.py:429/442/460`), а рёбер
у `Entity` нет вовсе (только `Source-CONTAINS-Chunk`, `orchestrator.py:470-472`).
`OPTIONAL MATCH` по соседям всегда пуст — графовая ось вырождена в простое
сопоставление узлов. Эксперимент без типизированного графа измерит заглушку.

Правила:

- `ExtractStage` получает второй путь: LLM-извлечение по `extraction.prompt_template`
  профиля (списки requirements/concepts/contracts + связи) под env-флагом
  (`EXTRACT_LLM=true`); детерминированный fallback (слова ≥5 символов) остаётся —
  он нужен для fake/inmemory-режимов и тестов, где LLM недоступен.
- `CommitStage._write`: метка узла определяется типом сущности (`Requirement`,
  `Concept`, `Contract`), `unique_key` — из онтологии (`id`/`canonical_name`); рёбра
  строятся по извлечённым парам и типам `edge_types`; `Source`/`Chunk` не меняются.

### Schema-провижининг (новый контракт)

Docs обещают автоматическое создание схемы при активации профиля
(`docs/01 §3`, `docs/data_model.md §1/§75`, invariant L2-01), но в коде этого нет:
`POST /api/v1/config/domain/activate` делает только `set_active_profile`
(`config_service/app.py:110-125`), constraints в Neo4j никто не создаёт, а
glossary-service граф не трогает (только словарь терминов). Без DDL MERGE по
`unique_key` не гарантирует уникальность канонических узлов, а валидационные
Cypher-правила профиля (`validation.rules`) не выполнимы.

Решение:
- `GraphStoreProvider.ensure_schema(node_types) -> None` — создаёт
  `CREATE CONSTRAINT ... IS UNIQUE` по `unique_key` каждого типа (идемпотентно по
  имени constraint; fallback-типы `Source`/`Entity`/`Chunk` пропускаются);
  InMemory — no-op (декларативно, L1-02: ядро не знает вендора, capability-паттерн
  ADR-024).
- Вызов: при активации профиля (по доке §3) ИЛИ при первом COMMIT домена —
  решение по факту теста 1.6: у config-service нет графических credential'ов и
  адаптера, поэтому фактическая точка = первый COMMIT домена в CommitStage
  (идемпотентный `ensure_schema` перед первой записью); доки §3 уточнить (ADR-029).
- Приёмка: `SHOW CONSTRAINTS` содержит constraint на каждый `node_type` онтологии;
  дубль канонического узла отклоняется движком (ConstraintError — не-transient,
  UC12-07 уже калиброван).

### Узел «кто создаёт метки» (по концепции)

- Типы объявляет **владелец домена** декларативно в Domain Profile
  (CONCEPT.md §6: «сообщество может создавать свои профили»; `docs/01 §1`),
  а не оператор манипуляциями в Neo4j. Роль оператора в принятой модели —
  топология/инфраструктура (ADR-019), переключение адаптеров, жизненный цикл
  индексации; ручное создание узлов-меток в графе нигде не прописано и не
  поддерживается API.
- Материализация (constraints + канонические узлы) по концепции — автоматическая
  при активации; фактом исполнения становится `ensure_schema` (см. выше) и
  типизированный COMMIT.

### Приёмка предусловия после переингеста на Neo4j-стек в графе присутствуют все
  типы онтологии, warning `label does not exist` из логов исчезает, скелет для
  графо-терминов непуст (см. tasks 1.3/1.4).

## 1. Дизайн эксперимента

Парные прогоны на **одной** revision (fetch до прогона, `GET /api/v1/ingestion/revision`):

| Ветка | RETRIEVAL_GRAPH_ENABLED | Ось |
|---|---|---|
| baseline | `false` | vector-only |
| target | `true` | graph + vector (гибрид) |

Оценка вклада графа — **только на поднаборе вопросов с `golden_graph_evidence`**
(графо-специфичных или где граф необходим для полноты). На остальных вопросах
различие веток — шум (vector достаточен), их в вывод про вклад не включаем.

Критерии:
- `правильность` пары оценивается по `golden_facts` (лично/постобработкой на qa_log);
- `вклад графа` = разность качества на графо-специфичном срезе при включённой оси;
- без `golden_graph_evidence` записывать «вклад не измеряем» — не интерпретировать.

## 2. Разметка источников по осям

Сейчас `pipeline.py:185` `build_sources(body_chunks)` — только векторное ось.
Графовый скелет `skeleton_rows` (стр. 163) в `done.sources` отсутствует (ED10-04).

Изменение:
- `build_sources` принимает и скелет; итог — `[{source_url, score, kind(axis)}]`,
  `axis ∈ {graph, vector}`; дедупликация по `(source_url, axis)`.
- `done.sources` расширяется (аддитивно, ADR-016): поле `axis`.
- В контекст сборки и в LLM контекст ничего не меняется на этой стадии
  (структура assemble/промпты не трогаем), меняются только источники в `done`.

## 3. Метрики вклада графа (`src/graphrag_proto/eval/metrics.py`)

Новая функция:
```
graph_contribution(retrieved_by_axis, golden_sources, golden_graph_evidence) -> {
    recall_graph,             # Recall@K по golden sources, где axis=graph
    recall_vector,
    evidence_recall_graph,    # Recall по golden_graph_evidence
    necessity,                # доля фактов, не покрытых vector-источниками
    delta_recall,             # recall(target) - recall(baseline) по срезу
}
```
Логика вычислительная, judge не нужен (важно для fast-loop, L1-05).

## 4. Формат вопроса v2 (ADR-015 delta)

Поля (все кроме обязательных — опциональные; обязательные: `id, query,
golden_sources, golden_facts`):

| Поле | Значение | Пример |
|---|---|---|
| `reasoning_type` | single-hop, cross-document, temporal-cross-document, contradiction, causal-risk, multi-hop | `multi-hop` |
| `answerability` | answerable / unanswerable | `answerable` |
| `as_of` | дата среза знаний | `2026-09-17` |
| `evidence_sections` | разделы документов | `["Retriever §2", "L3-03"]` |
| `evidence_policy` | joint / any / graph_required | `graph_required` |
| `rubric` | инструкция для проверяющего | `Назвать оба свидетельства` |
| `golden_graph_evidence` | отметка, что факт требует графового пути | `true` |

`evidence_policy=graph_required` влечёт `golden_graph_evidence: true`.
Атомарные facts: одна строка `golden_facts[i]` ≈ один проверяемый факт.

## 5. Артефакты прогона

Прогон оставляет **послойный след**: дешёвые слои обязательны всегда (в том числе
без судьи — инвариант «оценка не зависит от железа судьи»), тяжёлый
диагностический слой — по требованию. Методика слоёв —
`learning/prototype_test_plan_learning.md` §5; операционные правила —
`docs/test_plan.md` §5.

| Слой | Артефакт | Обязателен | Что отвечает |
|---|---|---|---|
| 1. Условия | `<out>/run_manifest.json` | да | «что именно сравнивалось» |
| 2. Пары | `<out>/qa_log.jsonl` (append) | да | «что ответила система и что нашла по каждому вопросу» |
| 3. Агрегат | `<out>/lift_report.json` + `.md` | да | «итоговые метрики и вердикт» |
| 4. Трасса | `<out>/trace.jsonl` (append, флаг `--trace`) | по требованию | «как данные шли внутри одного вопроса» |

Порядок обязательности: слои 1–3 пишутся **в любом режиме**, включая `--no-judge`
и `--retrieval-only` (иначе fast-loop не оставляет проверяемого следа). Слой 4 не
подменяет слои 1–3 и не заменяет метрики — это диагностика отдельного случая.

### 5.1 Манифест условий (`run_manifest.json`)

Без манифеста парность прогонов (§3.1) недоказуема: различие веток должно быть
восстановимо по файлам, а не по памяти. Записывается один раз в начале прогона.

```json
{
  "run_id": "eval_17898...",
  "started_at": "2026-09-17T12:00:00Z",
  "code_commit": "39b08af",
  "domain": "it",
  "revision": "39b08aff...",
  "revision_fingerprint": "sha256:...",
  "datasets": ["it/questions.jsonl", "it/questions_graph.jsonl"],
  "k": 5,
  "mode": "both",
  "graph_enabled": [true, false],
  "components": {
    "embedder": "bge-m3", "dimensions": 1024,
    "reranker": "noop",
    "chunker": "sliding_window", "chunk_size": 512, "chunk_overlap": 64
  },
  "generation": {
    "llm_adapter": "openai", "model": "...", "temperature": 0.0,
    "judge_adapter": "none"
  },
  "flags": ["--no-judge"]
}
```

Правило парности: **если два манифеста отличаются больше чем в одном поле
фактора (`mode`/`graph_enabled`) — прогоны не парные**, и разность метрик между
ними не является вкладом графа.

### 5.2 Журнал пар «вопрос–ответ» (`qa_log.jsonl`)

Инкрементально (append) на каждый вопрос, имя `<out>/qa_log.jsonl` — переживает
прерывание/падение в середине прогона. Строка:

```json
{
  "run_id": "eval_17898...",
  "id": "it_001",
  "mode": "target",
  "graph_enabled": true,
  "query": "...",
  "revision": "39b08aff...",
  "retrieved": [{"source_url": "...", "axis": "graph", "score": 0.7}],
  "golden_sources": ["..."],
  "golden_facts": ["..."],
  "golden_graph_evidence": true,
  "retrieval": {"recall_at_k": 0.8, "..."},
  "answer": "...",
  "generation": {"groundedness": 0.0, "mode": "n/a"},
  "timings": {"retrieval_time_s": 3.1, "generation_time_s": 18.4, "total_time_s": 21.5}
}
```

`generation.mode`: `judge` / `n/a` (без судьи) / `skipped` (retrieval-only).

### 5.3 Диагностическая трасса (`trace.jsonl`)

Слой 4 — диагностический, не измерительный: включается флагом `--trace`, при
обычном прогоне файл не создаётся. Отвечает на вопрос «почему контекст собрался
именно так», когда слоя 2 для разбора не хватает. Строка — один вопрос, append;
внутри — события этапов, которые пайплайн уже эмитит
(`embedding`/`cache`/`graph`/`vector`/`rerank`/`llm`/`done`):

```json
{
  "run_id": "eval_17898...",
  "id": "it_001",
  "query": "...",
  "events": [
    {"stage": "embedding", "ms": 42, "dimensions": 1024},
    {"stage": "cache", "hit": false},
    {"stage": "graph", "enabled": true,
     "skeleton_rows": ["Concept:OAuth ~[HAS_TAG]~ Source:oauth_doc"]},
    {"stage": "vector", "candidates": [{"chunk_id": "chk:...", "score_before": 0.61}]},
    {"stage": "rerank", "scores": [{"chunk_id": "chk:...", "before": 0.61, "after": 0.88}]},
    {"stage": "llm", "prompt_tokens": 3120, "dropped_chunks": 2},
    {"stage": "done", "total_time_s": 21.5}
  ]
}
```

Три правила трассы:

1. **Не подменяет метрики.** Слои 1–3 пишутся независимо; наличие или отсутствие
   трассы не меняет ни recall, ни вердикт.
2. **Скелет обязателен в трассе.** Без строк `render_skeleton` нельзя проверить,
   что именно граф отдал в контекст (слой 2 несёт источники, но не текст скелета).
3. **Вытеснение фиксируется.** `dropped_chunks` и объём контекста в токенах —
   единственный способ проверить, что скелет не вытесняется, а лимит действует
   на тело (инвариант L3-03).

## 6. Режимы запуска (`run_eval.py`)

| Режим | Судья | Генерация | Кейс |
|---|---|---|---|
| `EVAL_LLM_ADAPTER=openai` (default, как сейчас) | да | да | финальный полный прогон |
| `--no-judge` (эквивалент `EVAL_LLM_ADAPTER=none`) | нет | да, боевой LLM | fast-loop + ручной/пост-разбор пар |
| `--retrieval-only` | нет | нет | самый быстрый цикл (только источники и recall) |

`--no-judge` не требует судьи-контура в preflight; `lift_report` в этих режимах
считает только retrieval + graph_contribution, `verdict` помечается `n/a`
(валютное правило groundedness/coverage применимо только при judge).

## 7. Наборы и прогон

- `it/questions.jsonl` — переразметка 50 вопросов в формат v2 (категории сохранить).
- `it/questions_graph.jsonl` — новые графо-специфичные (цель: n ≥ 15, из них
  `graph_required` ≥ 8): multi-hop через пути графа, вопросы к инвариантам L1-xx,
  «где соединяются оси», противоречия между ревью и ADR (по образцу пилота).
- Раннер принимает `--dataset` как один файл либо список через `--extra-dataset`
  (несколько наборов мержатся, id сохраняются).
- После реализации — парный прогон `both` на живом стеке, ручной сэмпл ≥ 10 пар
  из qa_log (сан-чека: метрики не противоречат ручной оценке).