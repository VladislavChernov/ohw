// Из какого документа извлечена связь: у рёбер свои source_ids.
MATCH (a:ContextNode)-[r]-(b:ContextNode)
WHERE a.canonical_name IN ['accepted_at','api','adr-012','aliases','abstract base class','adapters.yaml','adr-037']
  AND b.canonical_name IN ['get /config/adapters','vector-only baseline graph adapter','topology orchestrator','soft-delete','ssot','vector_index','concept.md']
RETURN a.canonical_name AS from_name, type(r) AS rel, r.source_ids AS edge_sources,
       r.origin AS origin, b.canonical_name AS to_name;
// Контроль: где именно упоминаются выбранные сущности (все документы).
MATCH (n:ContextNode) WHERE n.canonical_name IN ['accepted_at','get /config/adapters','vector-only baseline graph adapter','adr-012','topology orchestrator','aliases','soft-delete']
RETURN n.canonical_name AS name, n.source_ids AS sources, n.chunk_ids AS chunks ORDER BY name;