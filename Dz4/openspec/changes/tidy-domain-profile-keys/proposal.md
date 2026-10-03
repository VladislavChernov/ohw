# Proposal: разделить ключи Domain Profile, снести легаси и подключить LLM-параметры
## Аудит 2026-10-03: бандл разделён, часть A выполнена

Проверка по коду, а не по отметкам, дала три вывода, которые меняют содержание бандла.

**1. Пункт 2.1 опирался на опровергнутое основание и в нынешнем виде был бы регрессией.**
`context_assembly.max_tokens` объявлен мёртвым («лимит задан константой `CONTEXT_TOKEN_LIMIT`»),
но `retrieval/pipeline.py:918` читает его из профиля, и значение уходит в `ContextAssembly`.
Выполнение пункта удалило бы работающую настройку. Основание исправлено.

**2. Корневая причина мёртвых ключей — не «забыли», а снятая типизация.** ADR-031 удалил
`_validate_ontology`, `ensure_schema`, typed labels и runtime DDL, а ключи остались. У
`retrieval.cypher_template` был читатель — `GraphRetriever.retrieve()`, — но у метода **0
вызывающих** во всём проекте, потому что ось давно идёт через `expand()` адаптера. Это тот
же класс остатка, что и нода-якорь `Source`.

**3. Отметки в бандле не являются мерой реализации.** `data-revision-query-context` реализован
17 из 18 при **0 из 18** галочках. Основание для приёмки — код, а не чекбоксы.

### Что сделано (часть A — только удаление)

| предмет | подтверждение удаления |
|---|---|
| `retrieval.cypher_template` из трёх профилей | `tests/test_dead_profile_keys.py`, 14 тестов |
| `build_cypher`, `DEFAULT_TEMPLATE`, `NODE_LABEL_PLACEHOLDER`, `GraphRetriever.retrieve()` | `retrievers.py`; гард `test_no_typed_ontology_cypher_rules` |
| `validation.rules[].cypher` (искали `:Requirement/:Concept/:Contract`) | профили `it/library/cinema` |
| `canonicalization.nodes` (слои под типизированные метки) | там же |
| `namespaces.yaml`: блоки `retrieval:` и `flags:` | читаются только `domain`, `chunking`, `adapters`, `llm` |
| `infra_topology.yaml`: `endpoints.embeddings` | `TopologyClient` берёт `/api/v1/config/adapters` |
| `context_assembly.eviction`, `context_assembly.template`, `chunking.chunk_size_by_type` | профили; ноль чтений в `src` |

Гард `tests/test_dead_profile_keys.py` проверяет контракт **связностью**, а не запрещённым
списком: «ключа нет в профилях **или** его читает код». Вернуть остатки молча нельзя.

**`ontology.node_types[].unique_key` и `ensure_schema` оставлены намеренно.** Их читатель
(`neo4j.py:269`) не вызывается в ingest, но `tests/test_typed_graph.py:215` содержит гард,
который бросает `AssertionError` при попытке вызова, — то есть это оформленный эталон, а не
мусор. Удаление погасило бы гард.

### Что осталось за бандлом (часть B — добавление поверхности)

Пункты 3.1–3.3: протянуть `extraction.temperature` и `extraction.max_tokens` из профиля в
`LLMInference` и `orchestrator._extract_llm`. Сейчас решает env, поэтому влиять на
детерминизм экстракции из профиля нельзя — а без этого повторяемый замер невозможен.

Это **добавление**, а не уборка, и риски противоположны: уборка может только задеть живое,
добавление может изменить поведение экстракции. Поэтому части A и B разведены и не должны
идти одним шагом.

---


## Почему

В `domain_profile.*.yaml` накопились 13 ключей, которые выглядят как настройка, но ничего не
делают. Проверено рекурсивным поиском по `prototype/src/`:

| Ключ | Где | Кто читает |
|---|---|---|
| `extraction.llm_enabled` | `it.yaml:49` | `orchestrator._profile_llm_enabled` — работает |
| `extraction.prompt_template` | `it.yaml:50+` | `orchestrator._extraction_template` — работает |
| `extraction.temperature` | `it.yaml:81` | никто; рантайм берёт env `LLM_TEMPERATURE` (дефолт кода 0.3) |
| `extraction.max_tokens` | `it.yaml:82` | никто; рантайм берёт env `LLM_MAX_TOKENS` (дефолт кода 2048) |
| `extraction.model` | — | никто; рантайм берёт env `LLM_MODEL` |
| `context_assembly.max_tokens` | `it.yaml:133` | никто; лимит задан константой `CONTEXT_TOKEN_LIMIT = 4096` |
| `context_assembly.template.priorities` | `it.yaml:128` | никто |
| `context_assembly.eviction` | `it.yaml:134` | никто |
| `chunk_entity_edge` | `it.yaml:46` | никто; строки `chunk_entity_edge` в коде нет вообще |
| `canonicalization.*.layers` | `it.yaml:107-111` | никто; NFKC/ё→е/casefold захардкожены в `_identity_key` |
| `ontology.edge_types` | `it.yaml:29-40` | никто |
| `ontology.node_types[].unique_key` | `it.yaml` | только `GraphStoreProvider.ensure_schema`, который не вызывается |
| `validation.rules` | `it.yaml:84+` | никто |
| `retrieval.cypher_template` | `it.yaml:142` | `retrievers.build_cypher:201`, но этот путь мёртв (см. ниже) |

Дополнительно: `retrieval.graph_boost`, `expansion_direction`, `expansion_kinds`, `max_depth`,
`max_fanout`, `max_graph_nodes`, `graph_source_relevance` **читаются** конвейером
(`pipeline.py:378-503`), но отсутствуют во всех трёх профилях, поэтому всегда применяются кодовые
дефолты. Это обратная проблема: параметр рабочий, но не задокументирован и не задаётся доменом.

Висящие ключи вредны тем, что создают ложную управляемость: правка YAML не даёт эффекта, и это
видно только по совпадению значений. Совпадение `max_tokens: 4096` в профиле и в
`compose.eval-minimal.yaml:133` — случайность, а не согласованность.

Документация молчит: поиск по `docs/*.md` не находит ни `extraction.temperature`, ни
`extraction.max_tokens`, ни `extraction.model`. `docs/04_services_config.md` §5.2 перечисляет
нечитаемые ключи, но только для `namespace: retrieval` и `namespace: normalizer`.
`docs/01_ontology_and_domain_profile.md` §1.1-1.3 описывает три категории, но неполно.

**Отдельно про легаси.** `retrieval.cypher_template` и `ontology.node_types[].type` читались
конструктором `GraphRetriever` (`retrievers.py:74`) через `build_cypher`, но сам
`GraphRetriever.retrieve()` не вызывается ни одним production-путём: конвейер использует
`expand()`. Проверено: единственные совпадения `.retrieve(` в тестах относятся к
`VectorRetriever`. То есть легаси-путь не просто не используется — он ещё и тянет за собой
чтение мёртвых ключей профиля при каждом создании ретривера.

**Два переключателя на одно и то же.** LLM-экстракция включается и через env `EXTRACT_LLM`
(`orchestrator._llm_extraction_enabled`), и через `extraction.llm_enabled` в профиле
(`_profile_llm_enabled`). Источник правды невнятен: непонятно, что wins при расхождении.

**Ещё одна находка, найденная попутно.** `tag_id` принимается в объекте сущности от LLM, а
`_context_node_id` использует `entity.get("tag_id") or entity.get("node_id")` — то есть
галлюцинированный моделью `tag_id` становится идентификатором узла и **создаёт отдельную ноду**
вместо слияния по canonical key. Модели нельзя позволять назначать идентичность.

> **Обоснование уточнено 2026-09-27.** Находка и вывод верны, но вывод опирался на
> несуществующую цитату: «идентичность задаёт canonical key» — это формулировка профиля
> (`ontology.node_types[].unique_key`), а не инварианта. Прямое основание — `L1-05`:
> «детерминированность критичных шагов (tag identity, …) обеспечивается Python-кодом;
> LLM допускается только на optional enrichment». То есть приоритет ответа модели —
> нарушение инварианта, и находка обычный дефект, а не нерешённый выбор. L2-01 при этом
> не нарушается: он требует существования и стабильности `tag_id`, а не его происхождения.
> Полный разбор, границы правки и сломанные при первой попытке тесты —
> `Ingest/analitic/node_identity_who_owns_tag_id.md`.

## Что делаем

1. **Разводим ключи на три категории и фиксируем в `docs/01` §1.1-1.3** — таблица «работает /
   инертно / параметр домена», с указанием, кто читает каждую группу. Категория «инертно»
   должна быть исчерпывающей, чтобы новый висящий ключ было видно.

2. **Удаляем мёртвый хвост typed-ontology из профилей:** `ontology.edge_types`,
   `ontology.node_types[].unique_key`, `ontology.node_types[].properties`, `validation.rules`,
   `chunk_entity_edge`, `canonicalization.*.layers`, `context_assembly.template.priorities`,
   `context_assembly.eviction`. Это отменённая модель, а не желаемое поведение; держать её в
   профиле незачем. `ontology.node_types[].type` **оставляем** — он читается
   (`_profile_entity_payloads`, метки узлов).

3. **Сносим легаси-путь в коде:** `GraphRetriever.retrieve()`, `build_cypher`,
   `DEFAULT_TEMPLATE`, поля `self.cypher` / `self.node_labels`; удаляем `retrieval.cypher_template`
   из профилей. Проверено, что production-код через них не идёт. История остаётся в git, поэтому
   отдельный архив не нужен и не создаётся.

4. **`context_assembly.max_tokens` удаляем из профиля:** лимит 4096 — это инвариант `L3-04`
   («жёсткий программный лимит»), а не доменная настройка. Профиль не должен переопределять
   инвариант.

5. **Подключаем живые LLM-параметры.** `LLMInference.generate` получает необязательные
   `temperature` и `max_tokens` на вызов; значения приходят из профиля
   (`extraction.temperature`, `extraction.max_tokens`), при их отсутствии — из
   `build_llm` (env/дефолт), то есть уровень дефолтов не меняется. `_extract_llm` передаёт
   профильные значения. `extraction.model` удаляется из профиля: выбор модели — слот адаптеров
   (`LLM_MODEL`, ADR-022), а не параметр домена.

6. **Разводим `tag_id`:** запрещаем его в ответе LLM. Поле не входит в белый список полей
   сущности; `canonical_name` остаётся единственным источником идентичности. Для ручных тегов
   `tag_id` по-прежнему принимается — там он задаётся человеком. Запрет ставится именно в
   белом списке ответа модели, а не в `_context_node_id`, иначе погибнет ручной ввод.

7. **Сводим два переключателя в один источник правды:** `extraction.llm_enabled` в профиле —
   решение домена (поведение записи, §5.0). Env `EXTRACT_LLM` становится аварийным
   выключателем и может только отключить (`false`), но не включить. Это снимает вопрос
   «что wins» и оставляет eval-стенду возможность снять LLM одной переменной.

8. **Документация:** `docs/04` §5.2 приводится в соответствие (добавляются профильные ключи),
   `docs/adapters_specification.md` §2.4.1 и §2.4.5 теряют легаси-описание, `docs/data_model.md`
   §3 не меняется. В `docs/history.md` не пишем ничего — он исторический.

## Спека

- **Идентичность сущности, извлечённой моделью, определяется только `canonical_name`**
  (`_identity_key`: NFKC, ё→е, casefold, сжатие пробелов) — по `L1-05`, а не по `unique_key`
  профиля, который не применяется. Ручные теги сохраняют право задать `tag_id` явно: это
  пользовательское решение, а не вывод модели.
- **Параметры вызова LLM разрешаются по цепочке профиль → сборка адаптера → кодовый дефолт.**
  Профиль может задать `temperature`/`max_tokens`; отсутствие ключа означает «дефолт», а не 0.
- **Включение LLM-экстракции определяется профилем**; env может только выключить. Это уточнение
  правила `docs/04` §5.0 для конкретного случая и не отменяет правило.
- **Инвариант `L3-04` (лимит контекста 4096) не переопределяется доменным профилем.**
- `ontology.node_types[].type` остаётся частью контракта: из него выводится whitelist ключей
  ответа LLM (`Requirement` → `requirements`) и метки узлов.

## Проверка

1. `pytest`, `ruff check src tests`, `mypy src` в dev-контейнере с host `RUN_CODE_COMMIT` —
   обязательный гейт. **Гейт нельзя прогнать без контейнера; до его прогона коммит не делается.**
2. Регрессии (фактически в `tests/test_typed_graph.py` — прежняя формулировка указывала
   `tests/test_ingestion_document.py`, где этих проверок нет):
   - `extraction.temperature` из профиля доходит до тела запроса LLM;
   - отсутствие ключа в профиле даёт дефолт сборки адаптера;
   - `tag_id` в ответе LLM не влияет на идентичность узла, связи от адресации модельными
     `tag_id` отвергаются, ручной `tag_id` сохраняется;
   - неизвестное поле ответа даёт `ExtractionModelError` (**не** `ValueError`: сбой модели
     должен отличаться от «неизвестно что» по типу, иначе его нельзя классифицировать).
3. Регрессия на легаси: `GraphRetriever` больше не читает профиль; `build_cypher` отсутствует.
4. `tests/test_docs_consistency.py` и `tests/test_doc_links.py` — зелёные после правок доков.
5. Ручная проверка на стенде: смена `extraction.max_tokens` в профиле меняет объём ответа;
   `EXTRACT_LLM=false` гасит экстракцию независимо от значения в профиле.

## Открытые вопросы

- `extraction.model` удаляется, но если домену понадобится своя модель экстракции, это будет
  слот адаптеров, а не ключ профиля. Отдельная задача.
- Проводка `namespaces → QueryPipeline` (бандл `wire-retrieval-runtime-config`) не входит сюда,
  но пересекается: после неё часть read-time параметров придёт из Config Service, и профильные
  дефолты станут нижним уровнем. Порядок: сначала этот бандл, потом тот.
