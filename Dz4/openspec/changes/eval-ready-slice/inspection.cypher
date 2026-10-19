// Разведка графа набора `ready` — только чтение.
// Запуск:  docker cp inspection.cypher neo4j-inspect:/inspection.cypher
//          docker exec neo4j-inspect cypher-shell -u neo4j -p local-stand-not-a-secret -f /inspection.cypher
// Планка сверки: 717 ContextNode, 432 Chunk, 1731 связь (Demo/README.md).
// Том ohw-runtime_ready_neo4j НЕ удалять.

// 1. Инвентаризация: метки и типы связей.
MATCH (n) RETURN labels(n) AS labels, count(*) AS n ORDER BY n DESC;
MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC;

// 2. Вид связи между сущностями: в типе ребра или в свойстве kind.
MATCH (a:ContextNode)-[r]->(b:ContextNode) RETURN type(r) AS rel, r.kind AS kind, count(*) AS n ORDER BY n DESC;

// 3. Покрытие по документам: у каких источников есть узлы графа.
MATCH (n:ContextNode) UNWIND n.source_ids AS s RETURN s AS source_url, count(*) AS ctx_nodes ORDER BY ctx_nodes DESC;
MATCH (c:Chunk) RETURN c.source_url AS source_url, count(*) AS chunks ORDER BY chunks DESC;

// 4. Мостовые сущности: provenance покрывает несколько документов (механизм междокументного пути).
MATCH (n:ContextNode) WHERE size(n.source_ids) > 1
RETURN coalesce(n.canonical_name, n.tag_id) AS name, n.origin AS origin, n.source_ids AS sources, size(n.source_ids) AS docs
ORDER BY docs DESC LIMIT 40;

// 5. Качество provenance: узлы без source_ids и распределение по origin.
MATCH (n:ContextNode) RETURN size(n.source_ids) = 0 AS empty_sources, count(*) AS n;
MATCH (n:ContextNode) RETURN n.origin AS origin, count(*) AS n ORDER BY n DESC;

// 6. Главный запрос: междокументные цепочки 2-6 переходов (концы не делят документ).
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

// 7. Распределение длин путей — достижимы ли якоря в разные позиции.
MATCH path=(a:ContextNode)-[*1..6]-(b:ContextNode) RETURN length(path) AS depth, count(*) AS n ORDER BY depth;
