# Предложение: отвязать extraction-подсказку от доменной онтологии

> **Дата:** 2026-10-13 · **Автор:** ревью (team-senior-plus / Step_5)
> **Статус:** **предложение, не реализация.** Ни один файл проекта этим документом не изменён.
> Это план правок для владельца — по методологии spec-first: сначала решение, потом код и тесты.
>
> **Зачем.** Проект перешёл от тяжёлой typed-ontology к **лёгкому context graph**
> (ADR-031, `docs/05_adr_log.md:1279`): валидация онтологии, `ensure_schema`, edge-whitelist и
> runtime-constraints **удалены из ingest path**, граф — optional эксперимент вне критерия
> приёмки, `graph_boost=0` по умолчанию. Но в конфиге профиля осталась секция `ontology`
> (`node_types`/`edge_types`/…), из которой **генерируется промпт извлечения**. Она выглядит как
> «доменная модель», хотя по факту — **список строк-хинтов для LLM**. Это когнитивный долг:
> читатель верит, что тут есть схема предметной области, а системе нужны только типы/виды для
> текста инструкции. Предложение: убрать секру `ontology`, заменив её плоским явным блоком
> `extraction`-хинтов, и переименовать «домен» в «корпус/namespace» там, где речь об изоляции.

## 0. Факты, на которых построено предложение (проверено чтением кода)

### 0.1. Кто читает `ontology` сегодня — полный список потребителей

| Потребитель | Файл:строка | Что читает | Зачем |
|---|---|---|---|
| `render_schema_block` | `ingestion_service/pipeline/prompt_renderer.py:79-145` | `ontology.node_types[*].{type,gloss,emitted}`, `ontology.edge_types[*].{type,from,to}`, `ontology.chunk_entity_edge` | **генерация промпта**: список типов, виды связей, скелет JSON |
| `render_candidates_block` | `prompt_renderer.py:171-204` | список типов (через `declared_types`) | подпись «типы из онтологии» в блоке кандидатов (ADR-053) |
| `_profile_node_types` | `ingestion_service/pipeline/orchestrator.py:259-266` | `ontology.node_types[*].type` | список типов сущностей для пайплайна |
| `_profile_entity_payloads` | `orchestrator.py:278-282` | `node_types[*].type` → `plural_key` → `CONTEXT_NODE_LABEL` | маппинг «какой JSON-ключ модели → какая метка узла» |
| `plural_entity_key` | `prompt_renderer.py:60-76` + `orchestrator.py:269-275` | `node_type` (строка) | JSON-ключ массива (`requirement`→`requirements`) |
| `render_instruction` | `orchestrator.py:1128` | весь профиль | сборка итоговой инструкции + `extractor_version` от содержимого |
| `config_service/domain.py:67-71` | валидация | `ontology.node_types`/`edge_types` — должны быть списками | проверка **формы** на стороне Config Service |
| `profile_contract.py:46-60` | контракт | `READ_BY_INGEST` включает `ontology`; `READ_BY_INGEST_NODE_KEYS = {type, gloss, emitted}` | **сам документирует**, что `unique_key`/`properties`/`edge_types`/`chunk_entity_edge` профиля не читаются как данные |

### 0.2. Ключевой вывод из фактов

- **Ни один потребитель не трактует онтологию как схему/контракт.** `ensure_schema`
  (`retrieval/adapters/neo4j.py:259`, `base.py:355`) существует, но **ingest его не вызывает**
  (`profile_contract.py:57-59`: «`unique_key` упоминается только в `ensure_schema`, который
  ingest не вызывает никогда»). Валидация типов не выполняется — только рендер в текст.
- **`build_cypher`/подстановка `{node_labels}` больше нет в коде** (grep по `prototype` — пусто),
  хотя профили (`it.yaml:134-137`) ещё ссылаются на «`{node_labels}` подставляется из
  `ontology.node_types` в runtime». Это **мёртвая ссылка в доков/профилях**.
- **`edge_types` читается только для промпта** (какие виды связей назвать модели и правило
  «оба конца среди объявленных»). Для векторного baseline и для лёгкого графа эти виды —
  подсказка, а не ограничитель.
- `READ_BY_INGEST_NODE_KEYS = {type, gloss, emitted}` — то есть из каждого `node_type`
  реально нужны **три строки**: имя типа, глосс, список полей «что выдавать». Остальное
  (`unique_key`, `properties`) — декор, который ingest не читает.

---

## 1. Что именно предлагаю (суть)

**Заменить секцию `ontology` в профиле на плоский блок хинтов извлечения** и убрать слово
«онтология/домен-модель» там, где речь о подсказке. Новая форма профиля:

```yaml
# Было (тяжеловесная «онтология», из которой половина не читается):
ontology:
  node_types:
    - type: "Requirement"
      unique_key: "canonical_name"        # НЕ читается ingest
      properties: [ ... ]                  # НЕ читается ingest
      gloss: "правило или ограничение"     # читается (в промпт)
      emitted: ["canonical_name", "name"]  # читается (в промпт)
  edge_types:                              # читается (только виды/направление в промпт)
    - { from: "Requirement", to: "Concept", type: "REQUIRES_CONSTRAINT" }
  chunk_entity_edge: "MENTIONS"            # НЕ читается ingest

# Стало (то, что реально нужно — плоский хинт для LLM):
extraction:
  entity_types:                            # то, что было node_types[*].{type,gloss,emitted}
    - type: "Requirement"
      gloss: "правило или ограничение, предъявляемое к системе"
      emit: ["canonical_name", "name", "id"]
    - type: "Concept"
      gloss: "система, понятие или термин предметной области"
      emit: ["canonical_name", "aliases", "description"]
  relationship_types:                      # то, что было edge_types — только «kind» + концы
    - { kind: "REQUIRES_CONSTRAINT", from: "Requirement", to: "Concept" }
    - { kind: "CONTRADICTS", from: "Requirement", to: "Requirement" }
```

Почему это честнее:
- Остаются **только читаемые** поля (`type`/`gloss`/`emit`/`kind`/`from`/`to`). Уходят
  `unique_key` и `properties`, которые `test_dead_profile_keys.py` уже помечает как мёртвые.
- `edge_types` → `relationship_types`: имя перестаёт намекать на «графовую схему» и называет
  вещь своим именем — **виды связей, которые мы просим назвать модель**.
- Никакой потери функциональности: `render_schema_block` читал ровно `{type, gloss, emitted,
  type, from, to}` — это всё сохраняется под новыми именами.

**Что при этом НЕ трогаем (это не «онтология», а полезное):**
- `profile.name/description/version` — подпись корпуса и вход в `extractor_version`.
- `chunking`, `retrieval`, `context_assembly`, `extraction.llm_enabled/prompt_template` — конфиг.
- **Namespace/изоляцию корпуса** (`domain` в ключах `_chunk_id`/`_entity_node_id`, projection
  per-domain, cache `query:sc:<rev>:<domain>`). Это multi-tenancy, он нужен независимо от
  онтологии. Переименование «домен»→«корпус» — **отдельная, опциональная** задача (см. §5).

---

## 2. Пофайловый план правок

Ниже — точные файлы и что в них менять. Порядок: сначала код-читатели (одна функция чтения),
потом профили, потом гард/тесты, потом доки. **Каждый шаг — с прогоном гейта** (`pytest`,
`ruff`, `mypy` в dev-контейнере).

### 2.1. Читатели профиля (источник схемы для промпта)

**`prototype/src/graphrag_proto/ingestion_service/pipeline/prompt_renderer.py`**
- `_node_types` (`:50-52`) → читать `extraction.entity_types` вместо `ontology.node_types`.
- `_edge_types` (`:55-57`) → читать `extraction.relationship_types` вместо `ontology.edge_types`.
- `render_schema_block` (`:79-145`):
  - поле `emitted` (`:99`) → читать `emit` (или оставить приём **обоих** имён на переход;
    см. миграцию §3). Глосс (`:95`) — без изменений по смыслу, только новый источник.
  - `chunk_entity_edge` (`:115`) → **убрать**: ingest это не читает, а строка про «система
    строит ребро» — метод, её место в `METHOD_BLOCK` (общий текст), не в профиле.
- `render_candidates_block` (`:171-204`): `declared_types` по-прежнему список строк типов —
  теперь из `extraction.entity_types`. Формулу `types_line` не менять.
- `render_instruction` / `extraction_identity` / `instruction_fingerprint` — без изменений
  (они работают с готовой строкой).

**`prototype/src/graphrag_proto/ingestion_service/pipeline/orchestrator.py`**
- `_profile_node_types` (`:259-266`) → читать `extraction.entity_types` вместо `ontology.node_types`.
- `_plural_entity_key` (`:269-275`) — без изменений (это про строку типа, не про секцию).
- `_profile_entity_payloads` (`:278-282`) — без изменений (вызывает `_profile_node_types`).
- Комментарий `:1124` про «вид в `ontology.edge_types`, забытый в прозе» → обновить на
  `extraction.relationship_types`.
- Мёртвая ссылка в профиле на `{node_labels}` (в `it.yaml`, не в коде) — код не трогаем, правим
  профиль (§2.2).

**`prototype/src/graphrag_proto/ingestion_service/pipeline/profile_contract.py`**
- `READ_BY_INGEST` (`:46-55`): убрать `"ontology"`, убедиться, что `"extraction"` в наборе.
- `READ_BY_INGEST_NODE_KEYS` (`:60`) → переосмыслить: ключи внутри `extraction.entity_types` —
  это `{type, gloss, emit}`. Переименовать константу или завести `READ_BY_INGEST_ENTITY_KEYS`.
- Комментарии `:57-65` (про `edge_types`/`chunk_entity_edge`) → переписать под новую секцию.

**`prototype/src/graphrag_proto/config_service/domain.py`**
- `:10` — убрать `"ontology"` из списка обязательных секций (если там требуется), `"extraction"`
  по факту.
- `:67-71` — валидация `ontology.node_types/edge_types` (должны быть списками) → валидация
  `extraction.entity_types`/`relationship_types` (должны быть списками).


### 2.2. Профили (3 файла)

**`prototype/domain_profiles/domain_profile.it.yaml`, `.library.yaml`, `.cinema.yaml`**
- Секцию `ontology:` → переписать в `extraction.entity_types` + `extraction.relationship_types`
  (или влить в существующую `extraction:`). Убрать `unique_key`, `properties`, `chunk_entity_edge`.
- `it.yaml:134-137` (`retrieval:` комментарий про `{node_labels}`) → убрать мёртвую ссылку:
  подстановки меток в Cypher в коде нет.

### 2.3. Гард и тесты

**`prototype/tests/test_dead_profile_keys.py`**
- `DEAD_KEYS` (`:22-27`): добавить `"ontology"`, `"unique_key"`, `"properties"` в список «ключ
  либо отсутствует, либо читается». Это **закрепляет** результат: после правки профилей этих
  ключей нет, и тест не даст их вернуть. Логика теста (`:52-63`) уже правильная — она защищает
  от отката ADR-031 в обратную сторону.
- `test_no_typed_ontology_cypher_rules` (`:66-73`) — оставить, расширить на `relationship_types`,
  если появятся Cypher-правила.

**`prototype/tests/test_prompt_renderer.py`**
- Переключить фикстуры/ассерты с `ontology.node_types/edge_types` на
  `extraction.entity_types/relationship_types`. Логика рендера не меняется — меняется форма входа.

**`prototype/tests/test_profile_contract.py`, `test_domain_profiles_load.py`**
- Обновить ожидания: секция `extraction` (не `ontology`); константы `READ_BY_INGEST*`.

**`prototype/tests/test_profile_prompt_hygiene.py`, `test_extracted_relation_reaches_graph.py`**
- Проверить завязку на `node_types`/`ontology` в фикстурах; при необходимости переключить на
  `extraction.entity_types`.

### 2.4. Документация

- **`docs/01_ontology_and_domain_profile.md`** — переписать раздел про `ontology` под «хинты
  извлечения»; убрать `unique_key`/`properties` как read-ключи.
- **`CONCEPT.md` §3** — уже говорит «optional hints, не ontology gate»; усилить: секция
  называется `extraction`, а не `ontology`.
- **`docs/data_model.md`, `docs/glossary.md`** — убрать упоминания `node_types`/`edge_types`
  как схемы; сослаться на `extraction`-хинты.
- **`docs/invariants.md`** — инвариант L1-01 (домен-агностичность) дополнить: «ядро не знает
  ни онтологии, ни списка типов как схемы — только строку-хинт для промпта».
- Новое решение оформить **отдельным ADR** в `docs/05_adr_log.md` (spec-first), со ссылкой на
  ADR-031 (лёгкий граф) и ADR-053 (кандидаты). Это отмена секции `ontology`, а не тихая правка.

---

## 3. Миграция без поломки (переходный период)

Чтобы не ломать старые профили и внешних читателей одним коммитом, readers делают **приём обоих
имён** на переход:

```python
# prompt_renderer.py — чтение с обратной совместимостью (схема)
def _entity_types(profile):
    ext = _section(profile, "extraction")
    items = ext.get("entity_types")
    if items is None:                       # переход: старая секция ещё может быть
        items = _declared_list(_section(profile, "ontology"), "node_types")
    return [i for i in items if isinstance(i, Mapping) and i.get("type")]

def _emit_list(node_type):                  # emitted ИЛИ emit
    raw = node_type.get("emit")
    if raw is None:
        raw = node_type.get("emitted")
    return [str(p) for p in (raw or []) if isinstance(p, str)]
```

Порядок внедрения (каждый шаг — зелёный гейт):
1. Readers начинают читать **и старое, и новое** (код компилируется, старые профили работают).
2. Переписать 3 профиля на новую форму. Старое имя всё ещё принимается — гейт зелёный.
3. `test_dead_profile_keys` + контракт перевести на новую секцию; добавить `ontology` в `DEAD_KEYS`.
4. **Убрать** обратную совместимость из readers (коммит-X). Гейт зелёный.
5. ADR + доки.

Шаг 4 можно отложить — оставить приём старого имени навсегда как «legacy alias» дешевле, чем
держать две формы. Но тогда `ontology` остаётся в `DEAD_KEYS`-гарде как «или отсутствует, или
читается» — и чтение-алиас удовлетворяет условию. Решение владельца.

---

## 4. Риски и что проверить

- **`extractor_version` сменится у всех документов.** `extraction_identity` считает отпечаток
  **содержимого** инструкции (`prompt_renderer.py:213-224`). Смена секции меняет текст промпта →
  меняется версия → узлы в графе будут «другой версии». Это **ожидаемо и по замыслу** (новая
  инструкция = новое извлечение), но для демо со старым корпусом означает, что старые узлы и
  новые будут разных версий. Учесть в demo-readiness/eval: переиндексация или приём двух версий.
- **Глоссарий/natasha не затронуты.** Кандидаты (ADR-053) читаются из текста, не из онтологии;
  `render_candidates_block` берёт только список типов-строк — он сохраняется.
- **Внешние читатели профиля.** Если кто-то вне `prototype/src` парсит `ontology` (внешние
  интеграции, eval-скрипты) — найти до правки. Grep ограничен `prototype/`; стоит проверить
  `infra/eval`, `pre-ingest`, docs-примеры.
- **Границы этого документа.** Всё выведено из **чтения** кода, профилей, тестов и ADR.
  `pytest`/`ruff`/`mypy` **не запускались** (dev-контейнер, Docker недоступен). Схемы кода —
  иллюстративные, не слиты с реальными сигнатурами. Перед применением — решение владельца и
  прогон гейта в контейнере.

---

## 5. Отдельно: переименование «домен» → «корпус/namespace» (опционально)

Из анализа следует, что `domain` несёт два разных смысла: **(а) namespace/изоляция** (нужен) и
**(б) контейнер конфигурации извлечения** (нужен как конфиг, но не как «предметная область»).
Если хочется убрать когнитивный диссонанс «домен-платформа без домена», это **отдельная** задача
и **не входит** в данное предложение, т.к. затрагивает ключи БД/кэша/графа (`ent:{domain}:…`,
`query:sc:<rev>:<domain>`) и десятки вызовов. Делать только если владелец захочет; здесь —
констатация, что она возможна и изолирована от §1-§4.

