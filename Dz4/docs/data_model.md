# Документация: Схема данных (Data Model)

> **Статус:** primitive document + dynamic context graph

## 1. Технические anchors

- `Source` — источник документа (`source_url`, `domain`, `doc_type`).
- `Chunk` — фрагмент текста (`chunk_id`, `text`, `source_url`, `domain`, `index`).
- `CONTAINS` — техническая связь Source → Chunk.

Эти anchors не являются бизнес-онтологией и не требуют DDL constraints.

## 2. Context nodes

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

`tag_id` является ссылочным ключом. `canonical_name` и aliases — представления одного тега;
они не являются глобальным unique constraint. Domain Profile и glossary помогают разрешать
multilingual aliases, но не обязательны для записи.

## 3. Context edges

Фактический состав графа, проверенный по коду:

| Ребро | Тип | Кто пишет |
|---|---|---|
`Source -[:CONTAINS]-> Chunk` | generic relationship type | оркестратор, `_write` |
`Chunk -[:MENTIONS]-> ContextNode` | generic relationship type | оркестратор, `_write` |
`ContextNode -[kind]-> ContextNode` | kind в property | экстракция, приводится к верхнему регистру |

Связи между context nodes приходят из ответа модели: `kind` (или `type`) приводится к верхнему
регистру, при отсутствии подставляется `RELATED`. Промпт экстракции в Domain Profile ограничивает
набор видов закрытым списком (`REQUIRES_CONSTRAINT`, `CONTRADICTS`, `SIMILAR_TO`, `REFERENCES`)
и запрещает придумывать другие.

Edge может иметь `origin`, `confidence`, `source_ids` и custom properties. В Neo4j безопасный
generic relationship type может хранить kind в property, чтобы arbitrary user links не зависели
от синтаксиса relationship type. `Chunk-[:MENTIONS]-> ContextNode` связывает seed chunk с
context graph. Удалённые/неактивные source provenance исключаются из retrieval.

## 3.1 Обход графа

`expand()` не ограничивает обход видом ребра: по умолчанию он идёт в обе стороны по любому типу и
возвращает фактический вид последнего прыжка в поле `kind`. Направление задаётся
`retrieval.expansion_direction` и принимает `both` (по умолчанию), `out` или `in`; прежние значения
`parent` и `related` сохраняются как синонимы `out` и `in`. Необязательное сужение
`retrieval.expansion_kinds` ограничивает обход явным списком видов; пустое значение и `any`
означают отсутствие сужения.

Глубина ограничена тремя, дополнительно действуют ограничения на fanout и общее число узлов, так
что снятие фильтра по видам не снимает бюджеты.

> **История дефекта (исправлено).** Раньше обход фильтровал рёбра по одному виду: `PARENT` при
> `direction="parent"` либо `RELATED` во всех остальных случаях. Ни один production-путь не пишет
> `PARENT`, а набор видов из промпта экстракции с искомыми не пересекался, поэтому разворот
> возвращал пусто и запрос молча уходил в vector-only с причиной `empty_projection`. Смысл при
> этом не терялся: вид ребра и до, и после хранится и возвращается. Причина пустого результата
> разделена на `empty_projection` (рёбер нет) и `no_matching_edge_kinds` (сужение ничего не
> нашло), чтобы потеря графа больше не выглядела как штатная работа.

## 4. Vector metadata

Vector record содержит `chunk_id`, embedding и metadata:

```text
chunk_id, text, source_url, domain, index,
context_ids, tag_ids, enrichment_origin, revision, projection_revision
```

Custom enrichment metadata не должен теряться при нормализации adapter results. Offline job
обновляет только metadata через `update_vector_metadata`; embedding и vector body не
перезаписываются. Vector-only baseline может работать без graph projection;
`context_ids`/`tag_ids` становятся seeds только для optional graph experiment.

## 5. Projection lifecycle

Graph projection может быть построена двумя способами:

- **Inline:** best-effort enrichment после vector commit; ошибка не отменяет документ.
- **Offline:** идемпотентный replay корпуса, который строит/обновляет nodes/edges и backfill-ит
  vector metadata; readiness и projection revision записываются отдельно.

Для domain хранится `ProjectionState`:
`data_revision`, `projection_revision`, `config_fingerprint`, status (`pending`, `ready`,
`degraded`, `stale`, `failed`), lease, job id и counts. Job key включает domain и все revision
поля; повторный запись того же projection не создаёт дублей.

Пока projection отсутствует или отстала, vector-only baseline остаётся валидным. Это не
состояние failed document и не обязательный prerequisite для query.

## 6. Constraints и миграция

Runtime ingest не вызывает `_validate_ontology` или `ensure_schema` и не создаёт constraints
для `Requirement`, `Concept`, `Contract` или custom labels. Удаление уже созданных Neo4j
constraints — отдельная operator migration, не часть primitive ingest.

## 7. Версионирование

Документ версионируется по ADR-014. Изменение content создаёт новую версию; graph enrichment
и tags имеют отдельную projection lifecycle. До M6-Growth profile/pipeline contract загруженного
корпуса считается неизменным, а ручной clean rebuild — единственным документированным путём
для полной переиндексации.
