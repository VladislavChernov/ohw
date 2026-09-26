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
| `CONTAINS` | Техническая связь Source → Chunk |
| `MENTIONS` | Техническая связь Chunk → ContextNode |
| вид из экстракции | Связь ContextNode → ContextNode; набор задаёт промпт профиля, при отсутствии вида подставляется `RELATED` |
| `PARENT` | Вид, который ищет `expand()` при обходе вверх; пишется только в тестах |
| `RELATED` | Вид, который ищет `expand()` при обходе внутрь; также значение по умолчанию при записи |

Fixed labels, `unique_key`, Cypher validation rules и semantic constraints не являются
обязательными.

## Retrieval

Vector search выполняется первым. В target режиме seeds из vector metadata проходят bounded
graph expansion; path, depth, confidence и boost попадают в QA/trace. При недоступном graph
используется vector-only fallback с degraded marker.

## Optional Glossary Service

Glossary Service отображает aliases/переводы в `tag_id`/`canonical_name`. Он не блокирует
primitive ingest, если недоступен. Ручные tags имеют приоритет над AI suggestions.

## Adapter Layer

`GraphStoreProvider` и `VectorStoreProvider` независимы. Neo4j — выбранный backend прототипа,
не обязательная архитектурная зависимость.
