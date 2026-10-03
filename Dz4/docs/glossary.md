# Глоссарий терминов GraphRAG

> **Статус:** multilingual alias resolution для dynamic context graph

## Core Concepts

| Термин | Определение |
|---|---|
| **Context Graph** | Лёгкий разреженный граф контекстных узлов и связей; не тяжёлая академическая онтология |
| **Context Node** | Динамический тег/сущность со стабильным `tag_id`, preferred label и aliases |
| **Vector Retriever** | Семантический поиск chunks; baseline и источник graph seeds |
| **Graph Expansion** | Ограниченный traversal от chunk `context_ids` к связанным контекстам с boost |
| **Domain Profile** | Optional hints для chunking, extraction, glossary и assembly; не mandatory ontology |
| **Document Registry** | Реестр источников, content hash и версий |

## Tag identity

```text
tag_id: opaque stable id within domain
canonical_name: preferred display label
aliases: variants, synonyms, translations
origin: user | ai | system
```

`canonical_name` — label, а не универсальный constraint. Например, `Quicksort` и
«Быстрая сортировка» могут ссылаться на один `tag_id` через glossary/alias map. При
неоднозначности создаются отдельные кандидаты и необязательный merge proposal; AI не обязан
молча объединять теги.

## Graph vocabulary

| Generic kind | Назначение |
|---|---|
| `Chunk` | платформенный якорь: фрагмент текста, владелец задан полем `source_url` (ADR-046 п. 9) |
| `MENTIONS` | Техническая связь Chunk → ContextNode |
| вид из экстракции | Связь ContextNode → ContextNode; набор задаёт промпт профиля, при отсутствии вида подставляется `RELATED` |
| `PARENT` | Вид, который больше не ищет `expand()`; в production-пути не пишется (встречается только в тестах) |
| `RELATED` | Вид по умолчанию при записи, когда модель не вернула вид связи; `expand()` его не фильтрует |

Fixed labels, `unique_key`, Cypher validation rules и semantic constraints не являются
обязательными.

## Retrieval

Vector search выполняется первым. В target режиме seeds из vector metadata проходят bounded
graph expansion; path, depth, confidence и boost попадают в QA/trace. При недоступном graph
используется vector-only fallback с degraded marker.

**Хоп (hop)** — один переход по одной связи от узла к узлу. Цепочка
`страна → область → район → город → улица → дом` — это 5 хопов от дома до страны и 6
узлов: хопы считаются по стрелкам, а не по узлам. Глубина обхода — сколько хопов можно
пройти от стартовой точки (`*1..depth` в Cypher), а в отчёте каждая строка несёт
`length(path)` — своё число хопов.

**Глубина обхода (`max_depth`)** — параметр запроса (ADR-036), а не константа профиля:
запрос → Config Service → профиль → кодовый фолбэк. Допустимый диапазон 1..6
(`MAX_EXPANSION_DEPTH`); в профилях объявлен дефолт 3. Значение выше потолка не отвергается,
а зажимается и объявляется в ответе (`depth_clamped`), потому что молчаливый зажим —
это расхождение между тем, что попросили, и тем, что сделали.

**Ветвление (`max_fanout`)** — сколько соседей можно взять от **каждого** узла обхода.
Считается по узлу в обоих адаптерах. Это ширина, а глубина — длина: вместе они и
образуют форму ответа, а предел общей длины обхода задаёт `max_graph_nodes`.

## Optional Glossary Service

Glossary Service отображает aliases/переводы в `tag_id`/`canonical_name`. Он не блокирует
primitive ingest, если недоступен. Ручные tags имеют приоритет над AI suggestions.

## Adapter Layer

`GraphStoreProvider` и `VectorStoreProvider` независимы. Neo4j — выбранный backend прототипа,
не обязательная архитектурная зависимость.
