// Доснятие: междокументные цепочки (исправленная форма) и распределение длин.
MATCH path=(a:ContextNode)-[*2..6]-(b:ContextNode)
WHERE size(a.source_ids) > 0 AND size(b.source_ids) > 0
  AND NOT any(s IN a.source_ids WHERE s IN b.source_ids)
WITH path LIMIT 200
RETURN [n IN nodes(path) | coalesce(n.canonical_name, n.tag_id)] AS chain,
       [r IN relationships(path) | type(r)] AS rels,
       length(path) AS depth,
       head(nodes(path)).source_ids AS a_sources,
       last(nodes(path)).source_ids AS b_sources
ORDER BY depth DESC;
MATCH path=(a:ContextNode)-[*1..6]-(b:ContextNode) RETURN length(path) AS depth, count(*) AS n ORDER BY depth;
// Короткая форма: только концы цепочки, чтобы глазами читать состав.
MATCH path=(a:ContextNode)-[*2..4]-(b:ContextNode)
WHERE size(a.source_ids) > 0 AND size(b.source_ids) > 0
  AND NOT any(s IN a.source_ids WHERE s IN b.source_ids)
RETURN coalesce(a.canonical_name,'?') AS from_name, coalesce(b.canonical_name,'?') AS to_name,
       length(path) AS depth, head(a.source_ids) AS from_doc, head(b.source_ids) AS to_doc
ORDER BY depth DESC LIMIT 60;