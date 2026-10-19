# Design: primitive ingest и sparse context graph

## 1. Границы

Подсистемы разделяются на четыре слоя:

1. **Primitive document ingest** — reader, canonical document, chunks, embeddings и registry.
   Этот слой не требует профиля, AI, tags, links или graph ontology.
2. **Vector metadata baseline** — vector store хранит text, source/chunk provenance, revision и
   optional `context_ids`/`tag_ids`; этот контур самодостаточен для grounding.
3. **Optional graph projection** — manual tags/links и AI suggestions строят generic context
   nodes/edges. Projection может выполняться inline после vector commit или отдельным offline
   replay; ошибка не отменяет document ingest.
4. **Graph experiment retrieval** — только при готовой projection vector search дополняется
   bounded expansion и boost. При недоступном или устаревшем graph используется vector-only
   fallback с явной метрикой degraded.

## 2. Модель контекстного графа

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

ContextEdge {
  edge_id: opaque id,
  from_id: ContextNode.tag_id,
  to_id: ContextNode.tag_id,
  kind: вид, заданный экстракцией; пишется как есть,
  origin: user | ai | system,
  confidence: number | null,
  source_ids: list[string]
}
```

> **Исправлено 2026-09-26.** Раньше здесь было `kind: parent | related | mentions`. Это описание
> не соответствовало ни рантайму, ни остальным документам: `parent`/`related` были только
> фильтром обхода, а виды рёбер приходят из промпта экстракции (`REQUIRES_CONSTRAINT`,
> `CONTRADICTS`, `SIMILAR_TO`, `REFERENCES`), а технические — `CONTAINS` (Source → Chunk) и
> `MENTIONS` (Chunk → ContextNode). Ни один production-путь не писал `parent`. Подробности и
> история дефекта: `docs/data_model.md` §3 и §3.1, инвариант `L3-02a`.

`canonical_name` не является глобальным unique key. Один смысловой термин получает стабильный
`tag_id`; `canonical_name` и aliases являются его представлениями. Glossary/alias map связывает
`Quicksort`, `Быстрая сортировка` и другие варианты с одним `tag_id`. Если разрешение неоднозначно,
создаются отдельные кандидаты и сохраняется proposal на merge; AI не обязан silently объединять.

`Source` и `Chunk` — технические anchors, а не доменные ontology types. `MENTIONS` связывает
chunk с context node; пользовательские и AI-связи используют generic kind и properties, чтобы
не зависеть от Neo4j relationship type.

## 3. Ingest и построение optional graph

```text
read → chunk → embed → vector commit → optional graph projection
                                      ├─ inline enrichment
                                      └─ offline enrichment/rebuild
```

`vector commit` завершает primitive path. Дальше graph experiment может быть построен двумя
способами:

- **Inline:** best-effort enrichment сразу после vector commit; manual/AI failure только
  quarantines this projection.
- **Offline:** идемпотентный replay корпуса после ingest, включая backfill `context_ids` и
  `tag_ids` в vector metadata. Offline job может отставать, не меняя baseline.

Порядок не должен блокировать primitive path:

- `tags`, `links` и profile optional;
- пустые/unknown tags не являются ошибкой;
- AI failure/timeout quarantines enrichment and does not fail document persistence;
- user-provided tags and links have explicit origin and are never silently replaced by AI;
- a re-ingest of the same content is a document no-op; a later tag/link edit is a separate
  graph mutation event in the lifecycle stage, not a hidden content rewrite.

Document identity and domain isolation remain separate from graph identity. `domain` scopes the
tag cloud and adapters; a graph tag is not a global cross-domain node.

## 4. Baseline и graph experiment retrieval

**Baseline** выполняет только vector search с metadata и возвращает текст, sources и provenance.
Он не вызывает graph adapter и не зависит от наличия graph projection.

**Experiment** запускается только для готовой projection:

1. Execute vector search with the request embedding and domain/revision.
2. Collect `context_ids`/`tag_ids` from returned chunk metadata.
3. Ask the graph adapter for bounded expansion from those seeds. The default policy follows
3. Ask the graph adapter for bounded expansion from those seeds. Traversal is not restricted by
   edge kind: by default it walks any relationship type in both directions and reports the actual
   kind of the last hop per row. `direction` is `both` (default), `out` or `in`; the earlier
   values `parent` and `related` are kept as synonyms for `out` and `in`. An optional
   `retrieval.expansion_kinds` narrows traversal to an explicit set of kinds.
4. Merge expanded context into candidates, preserving seed chunk, path, depth, edge kind and
   confidence.
5. Apply the context budget and, only when `retrieval.graph_boost` is set, a boost; graph
   evidence is attributable.
6. Return graph provenance and expansion trace.

The graph adapter must support a semantic operation such as `expand(context_ids, direction,
kinds, max_depth, max_fanout, max_nodes)` rather than making the core construct vendor-specific
Cypher. Neo4j, lightweight embedded graph, and other adapters implement the same contract, and
normalise `direction` and `kinds` identically, so the same profile yields the same traversal
regardless of backend. Graph unavailable, stale or incomplete is a degraded baseline, never a
silent successful experiment.

Traversal depth is bounded by a shared cap (`MAX_EXPANSION_DEPTH = 3`) enforced by every
adapter. A profile may request more; the clamp is reported in the expansion trace as
`requested_depth`, `effective_depth` and `depth_clamped` rather than applied silently.

`retrieval.graph_boost` defaults to `0.0`, and no domain profile sets it, so by default the graph
contributes context and provenance but does not reorder vector chunks. Two properties of the
boost are worth stating because they limit what it can prove: it is added to the **reranker
score**, not to a similarity, so its magnitude is adapter-dependent; and it is applied at
`source_url` granularity, so every chunk of a document that contributed any context node is
boosted equally. It also does not affect the `necessity` and `delta_recall` metrics, which
compare retrieved source sets per axis and are therefore measurable at the default.

## 5. Eval

For the same corpus revision, chunker, embedding model, reranker and K:

- `baseline`: vector-only;
- `graph-experiment`: vector search followed by a ready graph expansion/boost;
- optional `graph-only`: diagnostic, never the primary comparison.

The manifest and QA log record seed chunk IDs, expansion paths, depth, boost, fallback and
per-stage evidence. A graph-experiment run that silently falls back to vector-only is not counted
as a successful graph comparison. Offline projection readiness and revision are also recorded.

## 6. Migration from the typed-ontology work

The current typed labels, `_validate_ontology`, `ensure_schema` and constraints are not extended;
they are superseded by this change. Preserve source/chunk IDs, adapters, provenance, revision and
eval artifact infrastructure where they do not imply ontology semantics. Existing Neo4j
constraints/data cleanup is an operator migration and remains outside this corrective change.
A graph projection may be absent, built inline or rebuilt offline; none of these states changes
the vector-only baseline.
