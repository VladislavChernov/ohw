# Взаимодействие графа и вектора в Dz4 — ответы на три вопроса

> Источники: CONCEPT.md v5.0, ADR-013, `prototype/src/graphrag_proto/retrieval/retrievers.py`, `context.py`, `orchestrator.py`, `neo4j.py`, `docs/data_model.md §6`, `docs/03_retriever.md`, `eval-graph-contribution-experiment/design.md`.

---

## Вопрос 1. Что возвращает GraphRetriever — сырой JSON или отформатированный текст?

**Ответ: GraphRetriever возвращает сырой JSON-ответ Neo4j, но для промпта он рендерится в текст.**

### a) `GraphRetriever.retrieve()` → сырой список dict'ов

Файл: `prototype/src/graphrag_proto/retrieval/retrievers.py:46-63`

```python
def retrieve(self, query: str) -> list[dict[str, Any]]:
    terms = extract_query_terms(query)
    rows = self._graph_store.query(
        self.cypher,
        {"terms": terms, "max_nodes": self._max_nodes},
    )
    return [row for row in rows if row.get("n")]
```

Каждая строка — dict с полями `n`, `m`, `rel_type` — именно то, что вернула Cypher-колонка `RETURN n, m, type(r) AS rel_type`. Это **не текст**, не трёплеты, не Markdown — это сырой результат драйвера neo4j (`session.run(...).data()`), нормализованный через `normalize_graph_row()`.

### b) `ContextAssembly.assemble()` → форматирование в строки

Файл: `prototype/src/graphrag_proto/retrieval/context.py:32-61`

```python
def render_skeleton(rows: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    for row in rows:
        node = row.get("n") or {}
        neighbor = row.get("m") or {}
        rel_type = row.get("rel_type")
        label = _node_label(node)
        neighbor_label = _node_label(neighbor)
        if rel_type and neighbor:
            parts.append(f"{label} ~[{rel_type}]~ {neighbor_label}")
        else:
            parts.append(label)
    return parts
```

**Формат каждой строки:**
```
CanonicalName ~[REQUIRES_CONSTRAINT]~ NeighborCanonicalName
```
или, если соседа нет (изолированный узел):
```
CanonicalName
```

### c) Далее — сборка полного текста

```python
skeleton_parts = render_skeleton(skeleton_rows)
skeleton_text = "\n".join(skeleton_parts)
full = [skeleton_text] if skeleton_parts else []
if body_parts:
    full.append("\n".join(body_parts))
text = "\n\n".join(full)
```

**Итог:** GraphRetriever возвращает структурированные dict'ы (сырой граф), но для попадания в LLM они рендерятся в **текстовый формат `A ~[REL]~ B`** — не трёплеты, не Markdown, не Cypher-синтаксис. Это детерминированная Python-форматка, заданная в `context.py`.

---

## Вопрос 2. Как Cypher-шаблон понимает, за какие узлы n зацепиться в графе?

**Ответ: Никакого зашитого стартового идентификатора. Это полный скан + текстовый фильтр по терминам запроса.**

### a) Термины извлекаются из query

Файл: `retrievers.py:30-35`

```python
_TERM_RE = re.compile(r"[\wА-Яа-яЁё-]{3,}")

def extract_query_terms(query: str) -> list[str]:
    words = [w.casefold() for w in _TERM_RE.findall(query)]
    candidates = [query.casefold().strip(), *words]
    terms = list(dict.fromkeys([t for t in candidates if t]))
    return terms
```

Например, запрос «какие требования ссылаются на концепт OAuth?»:
- Полный запрос (casefold)
- Слова ≥ 3 символа: `какие`, `требования`, `ссылаются`, `на`, `концепт`, `oauth`
- Дедупликация → список терминов, передаваемый в Cypher как `$terms`

### b) Cypher-шаблон (DEFAULT_TEMPLATE)

Файл: `retrievers.py:19-27`

```cypher
MATCH (n)
WHERE any(t IN $terms WHERE toLower(n.canonical_name) CONTAINS toLower(t)
    OR toLower(n.name) CONTAINS toLower(t) OR toLower(n.id) CONTAINS toLower(t))
OPTIONAL MATCH (n)-[r]-(m)
WHERE m:{node_labels}
RETURN n, m, type(r) AS rel_type
LIMIT $max_nodes
```

После подстановки `node_labels` из онтологии (для IT-профиля: `Requirement|Concept|Contract`):

```cypher
MATCH (n)
WHERE any(t IN $terms WHERE toLower(n.canonical_name) CONTAINS toLower(t)
    OR toLower(n.name) CONTAINS toLower(t) OR toLower(n.id) CONTAINS toLower(t))
OPTIONAL MATCH (n)-[r]-(m)
WHERE m:Requirement OR m:Concept OR m:Contract
RETURN n, m, type(r) AS rel_type
LIMIT $max_nodes

### c) Как это работает концептуально

1. **`MATCH (n)`** — полный scan всех узлов в базе (без фильтра по метке).
2. **`WHERE any(...)`** — фильтр по текстовому вхождению: `canonical_name`, `name` или `id` узла содержит хотя бы один термин из запроса.
3. **`OPTIONAL MATCH (n)-[r]-(m) WHERE m:{node_labels}`** — для каждого найденного `n` ищутся соседи с метками из онтологии. Если соседей нет — строка всё равно возвращается (OPTIONAL), но `m` будет пустым.
4. **`LIMIT $max_nodes`** — ограничение количества возвращаемых строк (по умолчанию 5).

**Никакого anchor-узла, никакого старта от конкретного entity.** Это «текстовый семантический поиск по свойствам узлов + обход связей от найденных узлов». Не точечный lookup по ID.

### d) Можно ли задать стартовый узел?

В текущей реализации — **нет**. Cypher-шаблон в Domain Profile может быть кастомным (поле `retrieval.cypher_template`), но оба работают по принципу «текстовый матч по всем узлам → обход от них». Node_id-anchor в шаблоне не предусмотрен.

---

## Вопрос 3. Бизнес-сущности (Entities) в графе или только Chunk-цепочки?

**Ответ: В коде на данный момент — только тремя типами: Source, Entity, Chunk. Бизнес-сущности из онтологии (Requirement, Concept, Contract) физически отсутствуют.**

### a) Что реально пишет CommitStage._write (фактически)

Файл: `orchestrator.py:417-472`

```python
# Source: всегда
nodes = [{
    "node_id": source_id,          # src:{domain}:{source_url}
    "labels": [SOURCE_LABEL],      # "Source"
    ...
}]

# Сущности: ВСЕгда одна метка "Entity"
for entity in ctx.entities:
    canonical = str(entity.get("canonical") or entity.get("name") or "")
    nodes.append({
        "node_id": _entity_node_id(domain, canonical),
        "labels": [ENTITY_LABEL],  # "Entity" ← константа, а не тип из онтологии
        "properties": {
            "canonical_name": canonical,
            "source_ids": [...],
            "extractor_version": EXTRACTOR_VERSION,
            "variants": [...]
        }

**Результат в графе:**

| Узел | Метка | Откуда |
|---|---|---|
| Источник | `Source` | CommitStage._write |
| Сущность | `Entity` | CommitStage._write (все сущности — одна метка) |
| Чанк | `Chunk` | CommitStage._write |

Рёбра: только `Source -[CONTAINS]-> Chunk`. Между Entity-узлами рёбер нет.

### b) Что обещает Domain Profile (онтология)

Файл: `domain_profile.it.yaml`

```yaml
ontology:
  node_types:
    - type: "Requirement"
      unique_key: "id"
    - type: "Concept"
      unique_key: "canonical_name"
    - type: "Contract"
      unique_key: "id"
  edge_types:
    - from: "Requirement" to: "Concept" type: "REQUIRES_CONSTRAINT"
    - from: "Concept"    to: "Concept" type: "SIMILAR_TO"
    - from: "Concept"    to: "Contract" type: "REFERENCES"
    ...
```

**Эти типы объявлены, но не материализованы в графе.** Ошибка — зафиксирована в `eval-graph-contribution-experiment/design.md §0`:

> «Разбор логов M5 выявил: Cypher-шаблон графовой оси требует соседей `m:Requirement|Concept|Contract`, но загрузка пишет только `Source|Entity|Chunk`, а рёбер у Entity нет вовсе. OPTIONAL MATCH по соседям всегда пуст — графовая ось вырождена.»

### c) Почему так вышло

**ExtractStage — детерминированная заглушка.** Файл: `orchestrator.py:151-165`

```python
class ExtractStage(Stage):
    def run(self, ctx: PipelineContext) -> None:
        seen: set[str] = set()
        for chunk in ctx.chunks:

### d) Концепция vs реальность

| | Концепция (CONCEPT.md, ADR-006, Domain Profile) | Реальность (M1-M2 код) |
|---|---|---|
| Бизнес-сущности | Requirement, Concept, Contract — типизированные узлы с рёбрами | Только `Entity` — плоский список слов ≥ 5 символов |
| Рёбра между сущностями | REQUIRES_CONSTRAINT, CONTRADICTS, SIMILAR_TO, REFERENCES | Отсутствуют (только Source-CONTAINS-Chunk) |
| Связь граф-вектор | chunk_id связывает Chunk-узел и векторную запись; Entity-узлы связаны рёбрами и reachable из Cypher | chunk_id работает (векторная ось даёт чанки); Entity-узлы изолированы |
| Графовая ось в retrieval | Возвращает скелет: связанные сущности через рёбра | Возвращает изолированные Entity/Source/Chunk без связей (OPTIONAL MATCH пуст) |

### Итог по вопросу 3

- **Чанки — да, это отдельные узлы Chunk** с текстом, source_url, domain, embedding. Они существуют и в графовой оси (как узлы), и в векторной (по chunk_id).
- **Бизнес-сущности (Entities) — в графе есть, но в виде «Entity» без типов.** Это не Requirement/Concept/Contract из онтологии. Это плоские узлы-слова-сущности без рёбер между собой.
- **Граф не состоит только из Chunk-цепочек** — в нём есть Source и Entity, но связная структура (рёбра между сущностями, иерархии, зависимости) пока не создана. Граф вырожден в набор изолированных узлов, соединённых только с Source через CONTAINS.

Концепция предполагает типизированный граф, но фактическая реализация на текущий момент — упрощённая заглушка, которая работает для векторной оси (чанки есть, эмбеддинги есть) и для базового текстового поиска по свойствам, но не для обхода связей (скелет всегда пуст, кроме случаев, когда термины совпадают с text-свойствами самих узлов).

---

## Дополнительно: концепция взаимодействия (CONCEPT.md §2.1 + ADR-013)

### Фундаментальный принцип: две независимые оси

```
GraphStoreProvider (ABC)          VectorStoreProvider (ABC)
  query(cypher, params)            vector_search(embedding, top_k)
  upsert_nodes/edges              upsert_vectors(chunk_id, embedding)
  get_node/delete_node            delete_vectors(chunk_ids)
```

> «В гибриде граф и вектор — две независимые оси поиска, поэтому они представлены двумя изолированными ABC-контрактами, которые соединяются только на этапе Context Assembly.» — CONCEPT.md §2.1

**Физическая независимость:** в прототипе обе оси смотрят на один Neo4j, но концептуально они могут жить на разных движках: граф — Neo4j/Memgraph, вектор — Qdrant. Замена векторного хранилища на Qdrant — подмена одной реализации `VectorStoreProvider`, Cypher-пайплайны графа не затрагиваются (ADR-013).

### Точка соединения: Context Assembly

```
GraphRetriever.retrieve(query)  ─┐
                                  ├──▶ ContextAssembly.assemble() ─▶ LLM.generate()
VectorRetriever.retrieve(emb)   ─┘          (сборка промпта)
```

- Обе оси вызываются **параллельно** через `ThreadPoolExecutor` в `QueryPipeline.run()` (L1-04).
- **Скелет идёт первым**, векторные чанки — вторым.
- **Скелет никогда не вытесняется** при переполнении лимита токенов (4096); вытесняются только векторные чанки с наименьшим reranker-score.

### Связь осей на уровне данных: `chunk_id`

Это **единственная линия связи** между графовой и векторной осями в данных:

```
Графовая ось: узел Chunk { node_id: "chk:...", text, source_url, domain }
                ↑
                | chunk_id — общий ключ
                ↓
Векторная ось: запись { chunk_id: "chk:...", embedding: [1024 float], metadata }
```

- Узлы `Chunk` создаются в графовой оси (`CommitStage._write` → `upsert_nodes`).
- Их эмбеддинги кладутся в векторную ось по тому же `chunk_id` (`upsert_vectors(chunk_id, embedding)`).
- `VectorRetriever.vector_search` возвращает чанки по `chunk_id`, которые идут в Context Assembly как «тело».
- `GraphRetriever` возвращает структурные связи (узлы + рёбра), которые идут как «скелет».

> «Чанки текста хранятся в графовой оси как узлы `Chunk`; их векторные представления — в векторной оси по ключу `chunk_id` (upsert_vectors(chunk_id, embedding), размерность 1024 у bge-m3).» — docs/data_model.md §6