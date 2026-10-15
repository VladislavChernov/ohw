# Разведка графа `ready` (read-only)

Цель: получить фактические цепочки, из которых строится срез. Всё снимается **одним**
контейнером Neo4j на томе `ready`, без остального стенда, без записи в граф.

## Подъём (только по команде владельца)

```powershell
docker run -d --name neo4j-inspect -p 7474:7474 -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/local-stand-not-a-secret `
  -v ohw-runtime_ready_neo4j:/data neo4j:5.26-community
# ждём старта, затем:
docker exec -it neo4j-inspect cypher-shell -u neo4j -p local-stand-not-a-secret -f /inspection.cypher
```

Пароль — из `Demo/README.md`; на существующем томе `NEO4J_AUTH` игнорируется, том **не удалять**.
Файл `inspection.cypher` кладётся в контейнер (`docker cp`) либо запросы копируются из блоков
ниже в браузер `http://localhost:7474`.

## Ожидаемые числа (планка сверки)

Набор `ready`: **717 `ContextNode`, 432 `Chunk`, 1731 связь** (`Demo/README.md`). Совпадение —
значит поднялся тот том; расхождение — повод остановиться и выяснить, а не мерить дальше.

## Запросы и что отвечает

**1. Инвентаризация.** Все метки и типы связей — основа для остальных запросов.

```cypher
MATCH (n) RETURN labels(n) AS labels, count(*) AS n ORDER BY n DESC;
MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC;
```

Ожидание: метки `Chunk` и `ContextNode`; связи `MENTIONS` и виды из закрытого списка промпта
(`REQUIRES_CONSTRAINT`, `CONTRADICTS`, `SIMILAR_TO`, `REFERENCES`) либо `RELATED`. Если вид лежит
в свойстве `kind`, а не в типе ребра — это меняет запрос 5, и факт надо записать.

**2. Покрытие по документам.** Граф-полнота каждого документа: у каких источников есть узлы.

```cypher
MATCH (n:ContextNode) UNWIND n.source_ids AS s RETURN s AS source_url, count(*) AS ctx ORDER BY ctx DESC;
MATCH (c:Chunk) RETURN c.source_url AS source_url, count(*) AS chunks ORDER BY chunks DESC;
```

Сверяется с паспортом прогона `ready` (`documents_with_nodes_recorded`, `nodes_written`,
`enrichment_causes`). Документ с чанками, но без `ContextNode` — vector-only, в срез не берётся.

**3. Мостовые сущности.** Узлы, у которых provenance покрывает несколько документов. Это
единственный механизм междокументного пути (`data_model.md` §2.1), поэтому список — короткий и
главный.

```cypher
MATCH (n:ContextNode) WHERE size(n.source_ids) > 1
RETURN coalesce(n.canonical_name, n.tag_id) AS name, n.origin AS origin,
       n.source_ids AS sources, size(n.source_ids) AS docs
ORDER BY docs DESC LIMIT 40;
```

**4. Качество provenance.** Узлы без `source_ids` — источник не приписан; для среза такие узлы
бесполезны (не дают attribution), и их число — факт о наборе.

```cypher
MATCH (n:ContextNode) RETURN size(n.source_ids) = 0 AS empty_sources, count(*) AS n;
MATCH (n:ContextNode) RETURN n.origin AS origin, count(*) AS n ORDER BY n DESC;
```

**5. Междокументные цепочки.** Главный запрос: путь 2–6 переходов, у которого концы не делят ни
один документ (`source_ids` концов не пересекаются). Кандидаты в вопросы среза.

```cypher
MATCH path=(a:ContextNode)-[*2..6]-(b:ContextNode)
WHERE size(a.source_ids) > 0 AND size(b.source_ids) > 0
  AND NOT any(s IN a.source_ids WHERE s IN b.source_ids)
WITH path LIMIT 200
RETURN [n IN nodes(path) | coalesce(n.canonical_name, n.tag_id)] AS chain,
       [r IN relationships(path) | type(r)] AS rels,
       length(path) AS depth,
       startNode(path).source_ids AS a_sources,
       endNode(path).source_ids AS b_sources
ORDER BY depth DESC;
```

**6. Распределение длин.** Сколько материала на какую глубину — от этого зависит, achievable ли
срез с якорями в разные позиции (начало/середина/конец).

```cypher
MATCH path=(a:ContextNode)-[*1..6]-(b:ContextNode)
RETURN length(path) AS depth, count(*) AS n ORDER BY depth;
```

## Что записать по结果ам

В этот же бандл, файлом `graph-findings.md`:

- фактические числа против планки (717/432/1731) и метки/типы связей;
- список мостовых сущностей (запрос 3) с их документами;
- выборка цепочек (запрос 5) с позициями якорей — начало/середина/конец;
- документы, которые дали цепочки, и вердикт «граф-полны / vector-only»;
- пересечение с текстом: для каждой отобранной цепочки — подтверждена ли связь в документе.

Всё, что не подтвердилось текстом, записывается как факт об извлечении, а не отбрасывается молча.
