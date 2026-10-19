// Цепочки ТОЛЬКО между сущностями: без прохода через Chunk (MENTIONS даёт фиктивный мост).
MATCH path=(a:ContextNode)-[rels*2..4]-(b:ContextNode)
WHERE all(r IN rels WHERE type(r) <> 'MENTIONS')
  AND size(a.source_ids) > 0 AND size(b.source_ids) > 0
  AND NOT any(s IN a.source_ids WHERE s IN b.source_ids)
RETURN coalesce(a.canonical_name,'?') AS from_name, coalesce(b.canonical_name,'?') AS to_name,
       length(path) AS depth, head(a.source_ids) AS a_doc, head(b.source_ids) AS b_doc,
       [r IN rels | type(r)] AS rels
ORDER BY depth, from_name LIMIT 80;
// Самые связные узлы (хабы) — через них и идут междокументные пути.
MATCH (n:ContextNode)-[r]-() RETURN n.canonical_name AS name, count(r) AS degree, size(n.source_ids) AS docs ORDER BY degree DESC LIMIT 25;
// Сколько междокументных пар даёт наибridgéй узел: llm (6 документов).
MATCH (n:ContextNode {canonical_name: 'llm'})-[r]-(m:ContextNode)
RETURN n.canonical_name AS from_name, coalesce(m.canonical_name,'?') AS to_name, type(r) AS rel,
       size(m.source_ids) AS m_docs, m.source_ids AS m_sources
ORDER BY m_docs DESC LIMIT 40;