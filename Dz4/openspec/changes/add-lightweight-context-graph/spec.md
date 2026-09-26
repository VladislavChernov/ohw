# Спека: primitive ingest и sparse context graph

## Scenario: primitive ingest без AI и ontology

**WHEN** пользователь загружает документ без Domain Profile, LLM, tags или links

**THEN** ingest создаёт canonical document, chunks и vector records, а job завершается success;
отсутствие optional graph enrichment не является ошибкой.

## Scenario: metadata-only baseline

**WHEN** vector records contain text, source/chunk provenance, revision and optional tags without
a graph projection

**THEN** vector-only retrieval and grounded context assembly complete successfully; no graph
adapter is required and missing graph nodes do not make the document invalid.

## Scenario: optional manual tags and links

**WHEN** пользователь передаёт zero или больше tags и links вместе с документом

**THEN** система сохраняет их как optional graph enrichment с `origin=user`; пустой список
допустим; AI suggestions не перезаписывают manual data.

## Scenario: offline graph projection

**WHEN** an operator runs an offline enrichment/rebuild job after primitive ingest

**THEN** the job may build or update generic context nodes/edges and backfill `context_ids` in
vector metadata; the job is idempotent, records its projection revision/readiness, and does not
change the vector-only baseline while it runs.

## Scenario: shared tag cloud within a domain

**WHEN** новый документ ссылается на существующий tag в том же domain

**THEN** используется существующий `tag_id`; новый tag создаётся только если разрешение не дало
однозначного кандидата. `domain` изолирует облако тегов.

## Scenario: multilingual aliases

**WHEN** один алгоритм встречается как `Quicksort` и `Быстрая сортировка`

**THEN** glossary/alias resolution может связать варианты с одним `tag_id`; при неоднозначности
сохраняются отдельные кандидаты и merge proposal, без обязательного hard-coded словаря.

## Scenario: dynamic graph data

**WHEN** ingest получает custom tag, property или link kind

**THEN** graph adapter принимает их без `_validate_ontology`, fixed edge whitelist или DDL
constraints; технические проверки ограничивают только корректность endpoint и provenance.

## Scenario: graph experiment expansion

**WHEN** graph experiment retrieval выполняется при `graph_enabled=true` и projection готова

**THEN** сначала выполняется vector search, затем graph expansion по `context_ids` из metadata;
expansion имеет bounded depth/fanout/node budget и возвращает path/depth/confidence.

**AND** обход не ограничен видом ребра: по умолчанию он проходит по любому типу в обе стороны и
возвращает фактический вид последнего прыжка. Направление задаётся `retrieval.expansion_direction`
(`both` по умолчанию, `out`, `in`; `parent` и `related` — синонимы `out` и `in`), а необязательное
`retrieval.expansion_kinds` сужает обход до явного набора видов.

**AND** предел глубины применяется одинаково всеми адаптерами; урезание профиля фиксируется в
трассировке как `requested_depth`/`effective_depth`/`depth_clamped`, а не применяется молча.

## Scenario: graph boost

**WHEN** `retrieval.graph_boost` задан и expansion нашёл связанный context

**THEN** исходный vector rank сохраняется, а найденный контекст добавляется в provenance и
получает ограниченный boost.

**AND** boost по умолчанию равен `0.0`, и ни один доменный профиль его не задаёт, поэтому по
умолчанию граф даёт контекст и provenance, но не меняет порядок векторных чанков.

**AND** boost прибавляется к скору reranker'а, а не к similarity, и применяется на уровне
`source_url`: все чанки документа, давшего любой найденный узел, получают одинаковую прибавку.
На `necessity` и `delta_recall` boost не влияет.

## Scenario: graph unavailable or stale

**WHEN** graph adapter недоступен, projection ещё не построена или её revision отстаёт

**THEN** retrieval возвращает vector-only fallback; событие/метрика фиксирует degraded state;
это не выдаётся за успешный graph experiment.

**AND** при пустом результате обхода различаются две причины: `empty_projection` — рёбер нет,
`no_matching_edge_kinds` — сужение видами ничего не нашло. Раньше обе сводились к
`empty_projection`, из-за чего потеря графа выглядела как штатная работа.

## Scenario: no hard ontology DDL

**WHEN** выполняется primitive ingest

**THEN** runtime не вызывает `_validate_ontology` и `ensure_schema` и не создаёт constraints для
`Requirement`, `Concept`, `Contract` или custom labels. Существующая migration старой базы —
отдельная операция.
