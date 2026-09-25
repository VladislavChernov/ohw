# Документация: Domain Profile и контекстные теги

> **Статус:** corrective contract `add-lightweight-context-graph`

## 1. Назначение

Domain Profile задаёт область данных и optional hints для chunking, extraction и glossary.
Он не является обязательной онтологией, не валидирует каждый тег и не создаёт Neo4j constraints
во время ingest. Primitive ingest должен работать без профиля, LLM, tags и links.

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

Edges используют generic kinds `parent`, `related` и technical `mentions`; custom properties
и link kinds допустимы. `Source` и `Chunk` остаются техническими anchors, а не бизнес-типами.

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
