# Документация: Domain Profile и контекстные теги

> **Статус:** corrective contract `add-lightweight-context-graph`

## 1. Назначение

Domain Profile — это **опорная точка первого залития данных**, а не онтологический контракт.
Он задаёт область данных и optional hints для chunking, extraction и glossary. Он не является
обязательной онтологией, не валидирует каждый тег и не создаёт Neo4j constraints во время
ingest. Primitive ingest должен работать без профиля, LLM, tags и links.

### 1.1 Что из профиля реально действует

| Ключ | Как используется |
|---|---|
`chunking.*` (`strategy`, `chunk_size`, `overlap`, `*_splitter`, `*_parser`) | параметры чанкера |
`extraction.*` (`llm_enabled`, `prompt_template`, `temperature`, `max_tokens`, `template`) | экстракция сущностей и связей |
`ontology.node_types[].type` | **белый список типов сущностей**: какие типы нормализуются в context nodes. Всё материализуется под одной меткой `ContextNode` |
`retrieval.graph_search_enabled` | тумблер графовой оси: выключен по умолчанию |
`glossary`, `canonicalization` (синонимы) | разрешение вариантов и aliases |

### 1.2 Что объявлено, но не обеспечивается

Ключи ниже **не имеют эффекта в рантайме**. Их наличие в профиле не является гарантией, и
ссылаться на них как на работающий механизм нельзя:

| Ключ | Состояние |
|---|---|
`ontology.node_types[].unique_key` | единственный потребитель — `GraphStoreProvider.ensure_schema()`, который не вызывается из ingest path (ADR-031). Уникальность на уровне БД не обеспечивается |
`ontology.edge_types` | не читается нигде |
`validation`, `validation.rules` | проверяется только на тип «должен быть маппинг», не исполняется |
`chunk_entity_edge`, `chunk_size_by_type`, `eviction`, `language` | не читаются |

Практический вывод: если профиль объявляет `unique_key` или `edge_types`, это декларация о
намерении, а не о работающем ограничении. Идентичность контекстного узла держится на
стабильном `tag_id` в пределах домена (L2-01), а не на constraint.

### 1.3 Что профиль не должен решать

Профиль не обязан содержать онтологию, типы узлов и рёбер, whitelist связей или Cypher-правила.
Тяжёлая ontology-driven модель — артефакт раннего предположения о насыщенном графе, от
которого отказались: граф переведён в разряд optional offline experiment, где узлы — это
generic `ContextNode`, а рёбра — generic kinds.

## 2. Контекстное облако

Основная graph-модель — динамический набор context nodes и sparse edges:

```text
ContextNode {
  tag_id: opaque stable id within domain,
  canonical_name: preferred display label,
  aliases: list[string],
  origin: user | ai | system,
  confidence: number | null,
  source_ids: list[string],
  chunk_ids: list[string],
  properties: schemaless object
}
```

`canonical_name` — preferred label, а не универсальный constraint. Один смысловой объект
(например, алгоритм) получает стабильный `tag_id`; его варианты и переводы живут в `aliases`.
Glossary/alias resolution и optional AI dedup помогают связать `Quicksort` и
«Быстрая сортировка». Неоднозначные варианты не объединяются молча.

Edges записываются как generic kinds: технические `CONTAINS` (Source → Chunk) и `MENTIONS`
(Chunk → ContextNode), а связи между context nodes приходят из экстракции и приводятся к верхнему
регистру; при отсутствии вида подставляется `RELATED`. Набор видов для экстракции задаёт промпт
в профиле. `Source` и `Chunk` остаются техническими anchors, а не бизнес-типами. Соответствие
между записываемыми и обходимыми видами — см. `data_model.md` §3.

## 3. Optional enrichment

Пользователь может загрузить документ без тегов или вручную добавить tags/links. AI может
предложить enrichment автоматически. Оба источника используют один graph и сохраняют `origin`,
confidence и provenance; ручные данные не перезаписываются AI предложениями.

Ошибка AI enrichment не отменяет сохранение документа, chunks и vectors. Projection может быть
построена inline после vector commit или отдельным offline replay; offline job backfill-ит
`context_ids`/`tag_ids` и фиксирует readiness/revision, не меняя vector-only baseline. Изменение
только tags/links является отдельной graph mutation в lifecycle-стадии M6-Growth, а не скрытым
изменением `content_hash`.

### 3.1. Projection state

Для каждого domain job хранит `ProjectionState` с `data_revision`, `projection_revision`,
`config_fingerprint`, status (`pending`, `ready`, `degraded`, `stale`, `failed`), lease и
счётчиками источников. `ready` означает, что projection построена и проверена для текущей
revision; `stale` и отсутствие state запрещают graph experiment, но не запрещают vector-only
поиск. Offline rebuild использует claim/lease, детерминированный job key и безопасный retry.

## 4. Границы профиля

Профиль может содержать:

- `profile` и domain metadata;
- optional `chunking` settings;
- optional extraction prompt/parameters;
- glossary aliases и normalization hints;
- optional context-assembly template.

Профиль не обязан содержать `ontology.node_types`, `edge_types`, `unique_key` или Cypher
validation rules. Runtime не вызывает `_validate_ontology` и `ensure_schema`.
