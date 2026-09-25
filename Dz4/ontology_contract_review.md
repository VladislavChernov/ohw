# Ревью контракта онтологии и ингеста (подготовка Стадии 7)

> **Superseded 2026-09-25:** документ описывает старую typed-ontology линию. Актуальный
> контракт — vector-only baseline и optional graph experiment из
> `add-lightweight-context-graph`; обязательные labels, `_validate_ontology` и
> `ensure_schema` не переносятся в новый target.

**Дата:** 2026-09-23
**Бандл:** `openspec/changes/eval-graph-contribution-experiment` — стадии 1–6 закрыты; открыты 1.4/1.6 (live) и 7.1–7.5 (приёмка)
**Источники:** `prototype/domain_profiles/domain_profile.it.yaml`, `prototype/domain_profiles/ontology_matrix.csv`,
`prototype/src/graphrag_proto/ingestion_service/pipeline/orchestrator.py`,
`prototype/src/graphrag_proto/retrieval/adapters/neo4j.py`, `prototype/tests/test_typed_graph.py`

**Рамка решения (согласовано):**

- Эталон набора сущностей/рёбер — коробочный `domain_profile.it.yaml`
  (`REQUIRES_CONSTRAINT`, `CONTRADICTS`, `SIMILAR_TO`, `REFERENCES`): под него уже написаны
  `validation.rules` и `retrieval.cypher_template`.
- `ontology_matrix.csv` — **только справочник инвариантов для связки чанков**; YAML им не заменяем.

**Предмет:** почему графовая ось не получала узлов/рёбер (симптом M5) и что именно исправить
в конфиге и `orchestrator.py` до живой приёмки.

---

## 0. Где мы сейчас

Typed-граф в коде **уже реализован**: `ExtractStage._extract_llm` извлекает сущности/связи по
`extraction.prompt_template`, `CommitStage._write` пишет метки онтологии и рёбра между сущностями,
`ensure_schema` создаёт constraints, `GraphRetriever` получает непустой скелет. Это закрыто
unit-тестами (`tests/test_typed_graph.py`, 5 тестов), но **не подтверждено на живом Neo4j**
(задачи 1.4/1.6) и не прошло приёмку (7.1–7.5).

Перед живым прогоном найдены **нестыковки контракта**, из-за которых часть извлечённых связей
теряется молча, а constraints и валидация остаются декоративными. Ниже — по фактам кода.

---

## 1. Что в конфиге сейчас

`prototype/domain_profiles/domain_profile.it.yaml`:

| Секция | Что объявлено | Читает ли код | Комментарий |
|---|---|---|---|
| `profile` | name/description/language/version | нет | метаданные |
| `ontology.node_types` | `Requirement{unique_key: id; props: id,name,text}`, `Concept{unique_key: canonical_name; props: canonical_name,aliases,description}`, `Contract{unique_key: id; props: id,name,category}` | частично | `type` → метки узлов (retriever) и `ensure_schema`; `properties` не читаются никем |
| `ontology.edge_types` | `REQUIRES_CONSTRAINT`, `CONTRADICTS`, `SIMILAR_TO`, `REFERENCES` (с типами from/to) | только текстом в промпте | код типы рёбер не валидирует; связки «сущность ↔ Chunk» в онтологии нет вовсе |
| `extraction.prompt_template` | `id`, `system`, `user` (пример JSON), `temperature: 0.1`, `max_tokens: 4096` | `system`/`user` — да; `temperature`/`max_tokens` — нет | мёртвая конфигурация |
| `validation.rules` | Cypher-правила (`req_must_have_id`) | нет | YAML + валидация схемы в config-service |
| `canonicalization.nodes[Type].layers` | слои канонизации по типам | нет | фактическая канонизация — Glossary или `name` |
| `chunking` | 512/64 | да (`chunker._strategy_values`: env > профиль) | ок |
| `context_assembly` | `max_tokens: 4096`, `priorities: [graph, vector]` | `max_tokens` — да; `priorities` — нет | порядок «скелет → тело» захардкожен в `ContextAssembly` |
| `retrieval` | `graph_search_enabled`, `cypher_template` | да | ок |

`ontology_matrix.csv`: упоминаний в `docs/`, `openspec/`, compose-файлах — **0** (проверено
поиском). Используется как справочник инвариантов связки чанков (`HAS_CHUNK`, `EXTRACTED_FROM`,
`MENTIONS`), не как источник схемы.

---

## 2. Четыре механизма, которые ломают контракт

### М1. `canonical_name` от модели выбрасывается — ключом становится `name`

`_extract_llm` (`orchestrator.py:214–273`) считает `canonical = canonical_name or name`.
Но далее `NormalizeStage.run` (`342–378`) **перезаписывает** ключ:
`entity["canonical"] = glossary.canonical_name or entity["name"]`. При пустом `GLOSSARY_URL`
или недоступном glossary ключом становится **сырое `name`**. Дальше `DedupStage` (`379–408`)
склеивает по `(type, canonical)`, а `_write` (`619–728`) строит `by_canonical` и
`node_id = ent:{domain}:{type}:{canonical}`.

### М2. Рёбра ссылаются на имена, а матчинг идёт по canonical

`ctx.entity_edges` хранит сырые строки `from`/`to` из ответа модели. Резолв в `_write`:

```python
from_nodes = by_canonical.get(rel["from"], [])
to_nodes = by_canonical.get(rel["to"], [])
if len(from_nodes) != 1 or len(to_nodes) != 1:
    continue          # молча: ни лога, ни счётчика
```

Любое расхождение — регистр, лишние пробелы, `ё/е`, `name` вместо `canonical_name`, омонимия
между типами — даёт `!= 1`, и ребро **молча пропускается**. Это главная причина состояния
«узлы есть, а связей нет».

### М3. MERGE не падает — он молча ничего не делает

`_upsert_edges` (`retrieval/adapters/neo4j.py`):

```cypher
MATCH (a {node_id: $from}) MATCH (b {node_id: $to}) MERGE (a)-[r:TYPE]->(b) SET r += $props
```

Если endpoint не найден — MATCH даёт 0 строк, MERGE не выполняется, **ошибки нет**.
Hard-fail возможен по другой причине: `_safe_type(edge["type"])` бросает `ValueError` на тип,
не являющийся идентификатором (`depends on`, `REQUIRES-CONSTRAINT`) → **падает COMMIT всего
документа**. Плюс тип не сверяется с `ontology.edge_types` → дрейф модели данных: изобретённые
моделью типы попадают в граф.

### М4. `unique_key` и `properties` не соблюдаются при записи

`_write` пишет узлам-сущностям свойства `canonical_name`, `source_ids`, `extractor_version`,
`variants` — то есть `id` **никогда не пишется**, хотя `Requirement`/`Contract` объявляют
`unique_key: id`. Следствия:

- constraint `uniq_Requirement_id` / `uniq_Contract_id` — бутафория (реально работает только
  `uniq_Concept_canonical_name`);
- правило `validation.rules[req_must_have_id]` (`MATCH (r:Requirement) WHERE r.id IS MISSING`)
  при включении завалит **каждый** Requirement.

### Отдельно про «эхо-текст»

Требование возвращать текст чанка из промпта **уже убрано**: `extraction.prompt_template.user`
его не содержит; текст идёт только входным блоком («Текст документа для извлечения»,
`_render_extract_user`:308), а склейка сущностей с текстом и `chunk_id` выполняется в Python
(`ctx.chunks[meta["index"]]`, `_chunk_id`). Значит этот пункт плана — **верификация, а не
правка**. Единственная провокация эха — `text` в `ontology.node_types[Requirement].properties`:
если модель его вернёт, поле просто потеряется (код его не читает).

---

## 3. Как лучше сделать

### 3.1. Идентичность узла: один ключ на все типы

| Вариант | Как | Цена | Оценка |
|---|---|---|---|
| **A. `unique_key: canonical_name` для всех трёх типов** | правка YAML (2 строки): у `Requirement`/`Contract` ключ → `canonical_name`; `id` остаётся опциональным бизнес-свойством | согласовать `validation.rules` | **Рекомендую.** Одна ось идентичности; совпадает с уже работающим `Concept`; не требует от модели выдумывать `id` |
| **B. Как объявлено сейчас (`id` для Req/Ctr)** | промпт требует `id`, код его пишет, рёбра резолвятся по `id` | LLM генерирует id «из головы» («REQ-1», «API-1») → одинаковые id у разных сущностей в разных документах, склейка ломается; часто id в тексте просто нет | хрупко |
| **B′. Как объявлено, но `id` синтезируется в Python** | `id = slug(identity_key)` в оркестраторе; модель `id` не возвращает | два свойства с одинаковым содержимым (`id` и `canonical_name`) | приемлемый компромисс, если онтологию менять не хочется |

Независимо от выбора: **нормализовать ключ единой функцией** (`_identity_key()`: NFKC →
casefold → trim → при необходимости `ё→е`) и применять её **одинаково** к сущностям и к ссылкам в
рёбрах. Именно для этого объявлена и не реализована секция `canonicalization.layers` — дешевле её
реализовать, чем удалить.

### 3.2. Рёбра: ссылка на ключ + разрешение с фильтром по онтологии

1. **Промпт:** `relationships` ссылаются **только** на `canonical_name` из списков выше; явно
   «оба конца обязаны существовать в списках выше», «тип — только из перечисленных».
2. **Код:** индекс `by_key` (нормализованный ключ → кандидаты) из `canonical_name`, `name`,
   `aliases`, `variants`. Резолв по приоритету: точный `canonical` → единственный кандидат по
   варианту → **фильтр по `edge_types`** (`REQUIRES_CONSTRAINT` допустим только Requirement→Concept,
   `SIMILAR_TO` только C→C и т.д.) → иначе пропуск **с логом**.
   Типы `from`/`to` из онтологии дают детерминизм там, где сейчас стоит `!= 1 → skip`.
3. **Неизвестный тип ребра не должен валить джобу:** `warning` + пропуск (или маппинг в известный,
   если задан) + счётчик отброшенных пар как метрика качества извлечения.
4. **Связка Chunk ↔ сущность** (`MENTIONS` / `EXTRACTED_FROM`, как в матрице) — отдельным шагом:
   в `_extract_llm` уже есть цикл по чанкам, достаточно запомнить `chunk_id` у извлечённой сущности
   и построить ребро в Python. Это меняет модель данных → своим бандлом, не «заодно».

### 3.3. Свойства и constraints

Писать только объявленное онтологией. При варианте A — constraint `uniq_<Type>_canonical_name`
для трёх типов и переориентация `validation.rules` на `canonical_name` (либо честная пометка
секции как declarative-only). При варианте B′ — `id` пишется из Python, правила остаются как есть.

### 3.4. Промпт ↔ онтология: закрепить тестом-сторожем

Сейчас пример JSON требует у `Requirement` поля `canonical_name`+`aliases` (в онтологии их нет),
а `text` — есть в онтологии, но от модели не нужен. Тест-сторож: читать `domain_profile.it.yaml`
и проверять, что ключи примера JSON ⊆ `ontology.node_types[*].properties` и что `text` в примере
отсутствует. Это закрывает класс ошибок «промпт разошёлся с онтологией».

### 3.5. Мёртвые секции — применить или убрать

- `extraction.temperature` / `max_tokens` — либо прокинуть в LLM-адаптер ingest, либо убрать и
  жить на env (`LLM_MAX_TOKENS`, `LLM_TEMPERATURE`);
- `canonicalization.layers` — реализовать (см. 3.1);
- `context_assembly.priorities` — читать или убрать;
- `validation.rules` — исполнять (Cypher-проверки) или помечать как declarative-only.

Иначе они создают иллюзию управляемости.

---

## 4. Как провести по процессу

Изменения затрагивают **контракт идентичности** → это изменение модели данных (влияет на
`docs/data_model.md`, ADR, eval-корпус, `tests/test_typed_graph.py`), а не «правка промпта».
Порядок:

1. **Зафиксировать чистый слой.** Незакоммиченные файлы Этапа 15 (24 M + новые) провести через
   `pytest/ruff/mypy` в dev-образе `ohw/dz4-dev:0.1.0` и `/review` — правки контракта не должны
   ложиться на непроверенный слой.
2. **Оформить бандл** `fix-typed-graph-identity` (либо дельта стадии 1 в
   `eval-graph-contribution-experiment`, если он не архивирован). Задачи: выбор A/B, `_identity_key`
   + нормализация, резолв рёбер с фильтром по `edge_types`, неизвестный тип → warning,
   свойства/constraints/validation, промпт + тест-сторож.
3. Реализовать, прогнать чек и `/review`, закоммитить.
4. Только затем живая часть: переингест корпуса (1.4/1.6) со счётчиком типизированных узлов/рёбер
   по меткам, затем Стадия 7 (7.1–7.5).

---

## 5. Гигиена репозитория в этой итерации

- Удалён временный `Dz4/_tmp_changes.txt` (удаление застейджено).
- Аналитика **остаётся** в репозитории: строка `Ingest/analitic/` убрана из `.gitignore`
  (учебный проект, аналитика — часть проекта).
- В `.gitignore` добавлен `prototype/infra/.env`: там боевые `GRAPH_AUTH_API_KEY` и
  `NEO4J_PASSWORD`; файл был untracked, но не защищён от `git add -A`. Проверено:
  `git check-ignore` его теперь ловит.
- Не тронуто без решения: `Dz4/_review_bugs.md` (отчёт `/review`, находки перенесены в задачи
  `concurrent-ingest-write-policy`).

---

## 6. Открытые вопросы

1. **Идентичность:** вариант **A** (`canonical_name` для всех типов) или **B′** (`id` синтезируется
   в Python, онтология не меняется)?
2. **Связка сущность ↔ чанк** (`MENTIONS` / `EXTRACTED_FROM`): в этот же заход или отдельным бандлом?
3. **Форма:** новый бандл `fix-typed-graph-identity` или дельта в `eval-graph-contribution-experiment`?
4. **`_review_bugs.md`:** оставить как след ревью или убрать?

---

*Документ — рабочая аналитика подготовки Стадии 7; связан с `docs/test_plan.md` §5, ADR-029 и
бандлом `openspec/changes/eval-graph-contribution-experiment`.*


