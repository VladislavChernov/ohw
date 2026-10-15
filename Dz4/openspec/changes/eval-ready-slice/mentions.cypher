// Сколько MENTIONS у чанков каждого документа: у vector-only должно быть 0.
MATCH (c:Chunk)
OPTIONAL MATCH (c)-[m:MENTIONS]->()
WITH c.source_url AS source_url, c.chunk_id AS chunk_id, count(m) AS mentions
RETURN source_url, mentions, count(chunk_id) AS chunks
ORDER BY mentions ASC, chunks DESC;
