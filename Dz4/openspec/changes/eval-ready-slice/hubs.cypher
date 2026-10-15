// Топ узлов по числу связей + их соседи: под вопросы среза.
MATCH (n:ContextNode)-[r]-()
WITH n, count(r) AS degree
ORDER BY degree DESC LIMIT 10
MATCH (n)-[r]-(m:ContextNode)
WITH n, degree, collect(DISTINCT coalesce(m.canonical_name, '?')) AS neighbours
RETURN coalesce(n.canonical_name, '?') AS hub, degree, size(n.source_ids) AS docs,
       n.source_ids AS sources, neighbours[0..14] AS neighbours
ORDER BY degree DESC;
