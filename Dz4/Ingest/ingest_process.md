# Процесс ингеста в Dz4 — нарезка, сущности, привязки

> Источники: `orchestrator.py` (все этапы), `chunker.py`, `document.py`, `domain_profile.it.yaml`, `docs/02_pipeline_and_normalizer.md`, `docs/01_ontology_and_domain_profile.md`, `CONCEPT.md §4`, `ADR-021`.

---

## 1. Обзор: 9 этапов ingestion pipeline

Пайплайн прогоняет документ последовательно через 9 этапов (файл `orchestrator.py:50-60`):

```python
STAGES = (
    'INGEST',    # чтение документа ридером → канонический Document
    'CHUNK',     # нарезка блоков на чанки
    'EMBED',     # эмбеддинг каждого чанка + chunk_id
    'EXTRACT',   # извлечение сущностей (заглушка M1-M2, LLM — M3)
    'NORMALIZE', # glossary-разрешение каноничных имён
    'DEDUP',     # дедупликация по canonical_name
    'CONTRACT',  # заглушка — флаг contract: True
    'VALIDATE',  # базовая валидация сущностей
    'COMMIT',    # запись в GraphStore + VectorStore
)
```

**Вход:** канонический `Document` (ADR-021) — результат работы `DocumentReader.read()`.
**Выход:** узлы и рёбра в графе + эмбеддинги в векторном хранилище.

---

## 2. INGEST — чтение документа ридером

Файл: `orchestrator.py:86-99`

```python
class IngestStage(Stage):
    name = 'INGEST'

    def run(self, ctx: PipelineContext) -> None:
        reader = self._readers[ctx.doc_type]          # txt / md / pdf
        document = reader.read(
            Path(ctx.source_path), ctx.source_url, ctx.domain
        )
        ctx.document = document
```

Ридеры (папка `readers/`):
- `TxtReader` — читает `.txt` как есть
- `MdReader` — читает `.md`, сохраняет как text-блоки
- `PdfReader` (pypdf) — извлекает текст страниц, детектит code-блоки, изображения

Результат — `Document` (файл `document.py`):

```python
@dataclass
class Document:
    source_id: str
    source_url: str
    domain: str
    doc_type: str
    content_hash: str              # sha256 по нормализованному каноническому виду
    blocks: list[Block]            # type: text | code | image
```

**Важно:** пайплайн ниже (CHUNK..COMMIT) работает ТОЛЬКО с `Document`, никогда — с исходными байтами (ADR-021). content_hash считается с нормализованного канонического вида — источник-независимый (один хэш для .txt/.md/.pdf с одинаковым содержимым).
---

## 3. CHUNK — нарезка текста на чанки

Файл: `orchestrator.py:102-129`, `chunker.py`

Вход: `ctx.document.blocks` — список блоков типа text/code/image.
Обрабатываются только блоки типов `text` и `code` (image пропускается).

### 3.1 chunk_id — идентификатор чанка

Составляется на этапе EMBED, но зависит от индекса чанка:

```python
# orchestrator.py:37-40
def _chunk_id(source_url: str, index: int) -> str:
    digest = hashlib.sha256(f'{source_url}:{index}'.encode()).hexdigest()[:12]
    return f'chk:{digest}'
```

Пример: `chk:a1b2c3d4e5f6` — стабильный, детерминированный, зависит от source_url и порядкового номера чанка в документе.

### 3.2 Стратегии нарезки

В `chunker.py` реализовано 4 встроенные стратегии + плагины:

#### a) SlidingWindowChunker (дефолт, 512/64)

```python
# chunker.py:54-77
class SlidingWindowChunker(Chunker):
    def chunk(self, text: str) -> list[str]:
        words = text.split(' ')
        if len(words) <= self._chunk_size:
            return [text]
        chunks = []
        step = max(self._chunk_size - self._overlap, 1)   # 512-64 = 448
        for i in range(0, len(words), step):
            chunk = ' '.join(words[i : i + self._chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        return chunks
```

- Разбивает текст на слова по пробелу
- Скользящее окно: размер 512 слова, шаг 448 (overlap 64)
- Если текст короче 512 слов — один чанк целиком

#### b) StructureAwareChunker (Markdown-секции)

```python
# chunker.py:80-126
class StructureAwareChunker(Chunker):
    MARKDOWN_HEADER = re.compile(r'^\\s{0,3}#{1,6}\\s+.*$')

    def _split_sections(self, text: str) -> list[str]:
        # разбиение по заголовкам ## ... ###### (строки, начинающиеся с #)
        ...

    def chunk(self, text: str) -> list[str]:
        if len(text) <= self._chunk_size:
            return [text]
        sections = self._split_sections(text)
        if len(sections) <= 1:
            return self._sliding.chunk(text)   # fallback на sliding window
        chunks = []
        for section in sections:
            if len(section) <= self._chunk_size:
                if section.strip():
                    chunks.append(section)     # короткая секция — один чанк
            else:
                chunks.extend(self._sliding.chunk(section))  # длинная — нарезка внутри
        return chunks
```

- Сначала разбивает текст на секции по заголовкам Markdown (`#`, `##`, ..., `######`)
- Короткая секция (≤ 512 символов) — один чанк целиком
- Длинная секция — нарезается sliding window уже внутри секции
- Заголовок секции остаётся прикреплённым к её тексту (не уезжает в соседний чанк)

### 3.4 code-блоки не рвутся

```python
# orchestrator.py:122-128
for block in ctx.document.blocks:
    if block.type not in ('text', 'code'):
        continue         # image пропускается
    if not isinstance(block.data, str) or not block.data.strip():
        continue
    chunks += chunker.chunk(block.data)   # каждый блок чанкается отдельно
```

Код-блоки чанкиваются как отдельный текст — они не «размазываются» по соседним чанкам.

### 3.5 Результат CHUNK этапа

```python
ctx.chunks = ['chunk1 text...', 'chunk2 text...', ...]
ctx.chunks_meta = [
    {'index': 0, 'size_tokens': len('chunk1 text...')},
    {'index': 1, 'size_tokens': len('chunk2 text...')},
    ...
]
```

Из Domain Profile (`domain_profile.it.yaml`):

```yaml
chunking:
  strategy: 'sliding_window'
  chunk_size: 512
  overlap: 64
```

---

## 4. EMBED — эмбеддинги и chunk_id

Файл: `orchestrator.py:132-149`

```python
class EmbedStage(Stage):
    name = 'EMBED'

    def run(self, ctx: PipelineContext) -> None:
        for meta in ctx.chunks_meta:
            chunk = ctx.chunks[meta['index']]
            meta['embedding'] = self._embedder.embed(chunk, ctx.domain)
            meta['chunk_id'] = _chunk_id(ctx.source_url, meta['index'])
```

- Для каждого чанка вызывается `Embedder.embed(text, domain) → list[float]`
- На M1-M2: `DeterministicEmbedder(dim=8)` — стабильный хэш-вектор, без GPU
- На M3: bge-m3 (1024 dim) через Embeddings Service :8004
- chunk_id вычисляется: `chk:<sha256(source_url:index)[:12]>`

**chunk_id — это ключевое звено между графовой и векторной осями.** Он записывается:
- как `node_id` узла `Chunk` в графовой оси
- как `chunk_id` в записи векторного хранилища

---

## 5. EXTRACT — извлечение сущностей

Файл: `orchestrator.py:151-165`

### 5.1 Текущая реализация (M1-M2 заглушка)

```python
class ExtractStage(Stage):
    name = 'EXTRACT'

    def run(self, ctx: PipelineContext) -> None:
        seen: set[str] = set()
        for chunk in ctx.chunks:
            for token in chunk.split():
                word = ''.join(c for c in token.lower() if c.isalpha())
                if len(word) >= 5 and word.isalpha() and word not in seen:
                    seen.add(word)
                    ctx.entities.append({
                        'name': word,
                        'source': ctx.source_url,
                        'canonical': word
                    })
```

**Что делает:**
- Проходит по всем чанкам
- Для каждого токена (слова) — фильтрует: только буквенные символы, ≥ 5 букв
- Каждое уникальное слово → сущность с `name=word`, `canonical=word`, `source=source_url`

**Ограничения:**
- Не использует Domain Profile онтологию (не знает про Requirement/Concept/Contract)
- Не извлекает связи между сущностями (рёбра не создаются)
- Не создаёт типизированные узлы — все сущности → плоские `Entity` без типов

### 5.2 План M3 (LLM-извлечение)

Из `domain_profile.it.yaml`:

```yaml
extraction:
  prompt_template:
    id: 'extract_it_v1'
    system: 'звеньки сущности и связи из текста по доменной онтологии it.'
    user: |
      Формат вывода: JSON с списками requirements, concepts, contracts и
      связями REQUIRES_CONSTRAINT / CONTRADICTS / SIMILAR_TO / REFERENCES.
      Ответ только JSON, без пояснений.
  temperature: 0.1
  max_tokens: 4096
```

Из `eval-graph-contribution-experiment/design.md §1.1`:

> «ExtractStage получает второй путь: LLM-извлечение по extraction.prompt_template профиля (списки requirements/concepts/contracts + связи) под env-флагом (EXTRACT_LLM=true); детерминированный fallback (слова ≥5 символов) остаётся — он нужен для fake/inmemory-режимов и тестов, где LLM недоступен.»

**План:**
- Под флагом `EXTRACT_LLM=true` — вызов LLM по промпту из профиля
- LLM возвращает JSON: `requirements`, `concepts`, `contracts` + связи
- Сущности создаются с типом из онтологии (`Requirement`/`Concept`/`Contract`)
- Рёбра создаются по `edge_types` из профиля

Статус: задачи в `[ ]` (бандл `eval-graph-contribution-experiment`).

---

## 6. NORMALIZE — glossary-разрешение каноничных имён

Файл: `orchestrator.py:168-203`

```python
class NormalizeStage(Stage):
    name = 'NORMALIZE'

    def run(self, ctx: PipelineContext) -> None:
        if not self._glossary_url:
            return
        for entity in ctx.entities:
            body = json.dumps({'term': entity['name'], 'domain': ctx.domain}).encode()
            req = urllib.request.Request(
                f'{self._glossary_url}/api/v1/glossary/resolve',
                data=body, headers=headers, method='POST'
            )
            try:
                with urllib.request.urlopen(req, timeout=2) as resp:
                    payload = json.loads(resp.read().decode())
                canonical = payload.get('canonical_name') or entity['name']
            except Exception:
                canonical = entity['name']     # glossary недоступен → оставляем как есть
            entity['canonical'] = canonical
```

- Для каждой сущности отправляет POST на Glossary Service :8003 `/api/v1/glossary/resolve`
- Тело: `{'term': entity['name'], 'domain': ctx.domain}`
- Получает `canonical_name` — каноничное имя из глоссария
- Если glossary недоступен или ответ пуст — оставляет исходное имя

**Пример:** сущность `word='req'` → glossary возвращает `canonical_name='requirement'` → сущность обновляется.

---

## 7. DEDUP — дедупликация по canonical_name

Файл: `orchestrator.py:205-222`

```python
class DedupStage(Stage):
    name = 'DEDUP'

    def run(self, ctx: PipelineContext) -> None:
        canonical_map: dict[str, dict[str, Any]] = {}
        for entity in ctx.entities:
            key = entity['canonical']
            if key not in canonical_map:
                canonical_map[key] = {
                    'name': key,
                    'sources': [ctx.source_url],
                    'variants': [entity['name']],
                }
            else:
                canonical_map[key]['sources'].append(ctx.source_url)
        ctx.entities = list(canonical_map.values())
```

**Логика:**
- Группирует сущности по `canonical` (каноничному имени после NORMALIZE)
- Для каждой группы:
  - `name` = canonical
  - `sources` = список всех source_url, из которых пришла сущность
  - `variants` = список исходных имён (синонимы)
- Повторная загрузка того же источника не дублирует сущность

**Масштаб:** в M1-M2 — простая группировка по имени. В M3+ — двухступенчатая дедупликация (cosine ≥ 0.92 — auto-merge, 0.75-0.92 — LLM-верификация).

---

## 8. CONTRACT — заглушка иерархии

Файл: `orchestrator.py:225-232`

```python
class ContractStage(Stage):
    name = 'CONTRACT'

    def run(self, ctx: PipelineContext) -> None:
        for entity in ctx.entities:
            entity['contract'] = True
```

На M1-M2 — просто добавляет флаг `contract: True` всем сущностям. В M3+ — реальная склейка иерархии Contract-узлов по рёбрам EXTENDS/REFERENCES из онтологии.

---

## 10. COMMIT — запись в граф и вектор

Файл: `orchestrator.py:381-490+`

Это самый сложный этап. Он записывает:

### 10.1 DocumentRegistry (иdемпотентность)

```python
# orchestrator.py:407-415
def run(self, ctx: PipelineContext) -> None:
    doc = ctx.document
    result = self._registry.upsert(doc)          # SQLite: (domain, source_url, content_hash)
    ctx.registry_result = result
    ctx.commit_applied = True
    _doc_id, _version, created_new = result
    if not created_new:
        return       # idempotent no-op: контент не изменился → ничего не пишем
    self._write(doc, ctx)
```

DocumentRegistry следит за `(domain, source_url, content_hash)`:
- Совпадение → no-op (идемпотентность, ADR-014, L2-06)
- Изменение → version+1, старая версия помечается superseded

### 10.2 План записи: узлы и рёбра

Файл: `orchestrator.py:417-484`

**Узлы (3 типа):**

```python
# Source
nodes = [{
    'node_id': source_id,              # src:{domain}:{source_url}
    'labels': ['Source'],
    'properties': {
        'source_url': source_url,
        'domain': domain,
        'doc_type': doc.doc_type,
    }
}]

# Сущности (Entity — все без типов!)
for entity in ctx.entities:
    canonical = str(entity.get('canonical') or entity.get('name') or '')
    nodes.append({
        'node_id': _entity_node_id(domain, canonical),  # ent:{domain}:{canonical_name}
        'labels': ['Entity'],                           # ← всегда Entity, не Requirement/Concept/Contract
        'properties': {
            'canonical_name': canonical,
            'source_ids': list(entity.get('sources') or []),
            'extractor_version': EXTRACTOR_VERSION,    # 'deterministic:v1'
            'variants': list(entity.get('variants') or []),
        }
    })

# Чанки
for meta in ctx.chunks_meta:
    chunk_id = meta['chunk_id']
    text = ctx.chunks[meta['index']]
    nodes.append({
        'node_id': chunk_id,                               # chk:<digest>
        'labels': ['Chunk'],
        'properties': {
            'chunk_id': chunk_id,
            'text': text,
            'source_url': source_url,
            'domain': domain,
            'index': meta['index'],
        }
    })
```

**Рёбра (только Source → CONTAINS → Chunk):**

```python
edges = []
for meta in ctx.chunks_meta:
    chunk_id = meta['chunk_id']
    edges.append({
        'from_id': source_id,          # src:{domain}:{source_url}
        'to_id': chunk_id,             # chk:<digest>
        'type': 'CONTAINS',
        'properties': {}
    })
```

### 10.3 Связь осей через chunk_id

Это **единственная линия связи** между графовой и векторной осями:

```
GraphStore (Neo4j):
  узел Chunk { node_id: 'chk:a1b2c3d4e5f6', text: '...', source_url: '...', domain: '...' }

VectorStore (Neo4j/Qdrant):
  запись { chunk_id: 'chk:a1b2c3d4e5f6', embedding: [float...], metadata: {...} }
```

- `chunk_id` узла в графе = `chunk_id` записи в векторном хранилище
- `VectorRetriever.vector_search` возвращает чанки по `chunk_id`
- `GraphRetriever` возвращает структурные связи (узлы + рёбра)

Файл: `orchestrator.py:490-577`

```python
# _write продолжается...
stale_chunks = graph.list_chunk_ids_of_source(source_id)   # старые чанки источника
written_chunk_ids = [meta['chunk_id'] for meta in ctx.chunks_meta]   # новые чанки

# Проверка: атомарная пара или best_effort?
if _is_atomic_pair(graph, vector):
    # обе оси в одной транзакции движка
    with graph.atomic_batch() as batch:
        batch.upsert_nodes(nodes)
        batch.upsert_edges(edges)
        batch.upsert_vectors(vectors)
else:
    # best_effort: граф → вектор, при сбое вектора граф компенсируется
    _write_best_effort(graph, vector, nodes, edges, vectors, stale_chunks, written_chunk_ids)
```

**Атомарная пара** (`_is_atomic_pair`):
- `graph.consistency_capability() == 'atomic'` И `vector.consistency_capability() == 'atomic'`
- `graph.engine_key() == vector.engine_key()` (общий движок)

**best_effort** (по умолчанию):
1. Commit графа (успех → идём дальше)
2. Commit вектора
3. Если вектор падает → компенсация графа: `delete_node` всех записанных чанков, `delete_vectors` тех же чанков. Джоба → `failed` с пометкой «компенсировано».

---

## 11. Domain Profile и его влияние на извлечение

Файл: `domain_profile.it.yaml`

```yaml
ontology:
  node_types:
    - type: 'Requirement'
      unique_key: 'id'
    - type: 'Concept'
      unique_key: 'canonical_name'
    - type: 'Contract'
      unique_key: 'id'
  edge_types:
    - from: 'Requirement' to: 'Concept'      type: 'REQUIRES_CONSTRAINT'
    - from: 'Requirement' to: 'Requirement'  type: 'CONTRADICTS'
    - from: 'Concept'     to: 'Concept'      type: 'SIMILAR_TO'
    - from: 'Concept'     to: 'Contract'     type: 'REFERENCES'

extraction:
  prompt_template:
    id: 'extract_it_v1'
    system: 'звеньки сущности и связи из текста по доменной онтологии it.'
    user: |
      Формат вывода: JSON с списками requirements, concepts, contracts и
      связями REQUIRES_CONSTRAINT / CONTRADICTS / SIMILAR_TO / REFERENCES.
      Ответ только JSON, без пояснений.
  temperature: 0.1
  max_tokens: 4096
```

### Что из этого используется в M1-M2 ингесте:

| Секция профиля | Используется в M1-M2 ингесте? | Как |
|---|---|---|
| `profile` | Частично | `domain` используется как параметр |
| `ontology.node_types` | **НЕ используется** в коде | Только для Cypher-шаблона ретривера |
| `ontology.edge_types` | **НЕ используется** | Рёбра между Entity не создаются |
| `extraction.prompt_template` | **НЕ используется** | LLM-путь не реализован (заглушка ExtractStage) |
| `validation.rules` | **НЕ используется** | Валидация только базовая (имя + sources) |
| `canonicalization` | **НЕ используется** | NormalizeStage только glossary-резолв |
| `chunking` | **Используется** | Стратегия, chunk_size, overlap — резолвятся в ChunkStage |
| `context_assembly` | **Используется** в retrieval | max_tokens, eviction — в ContextAssembly |
| `retrieval` | **Используется** в retrieval | cypher_template, graph_search_enabled — в GraphRetriever |

---

## 12. Привязки сущностей — что сейчас и что планируется

### 12.1 Что сейчас (M1-M2)

```
Document → Чанки (text) → Сущности (слова ≥ 5 букв) → Entity-узлы в графе

Граф после коммита:
  Source ─[CONTAINS]→ Chunk
  Entity (изолированный, без рёбер)
  Chunk (связан с Source)
```

**Связи:**
- `Source -[CONTAINS]-> Chunk` — чанки привязаны к источнику
- Между Entity-узлами — **нет рёбер**
- Entity ↔ Chunk — **нет прямой связи** (сущности — слова, чанки — текст; между ними нет явной связи в графе)

**Итог:** сущности существуют как изолированные узлы `Entity` в графе, но не связаны ни с чем, кроме общего `source_url` (который хранится в `source_ids` свойства Entity).

### 12.2 Что планируется (M3+)

```
ExtractStage (LLM-путь):
  → requirements: [{id, name, text, ...}]
  → concepts: [{canonical_name, description, source_ids, ...}]
  → contracts: [{id, name, category, ...}]
  → связи: REQUIRES_CONSTRAINT, CONTRADICTS, SIMILAR_TO, REFERENCES

CommitStage._write:
  → метка узла: Requirement / Concept / Contract (не Entity!)
  → рёбра: по edge_types из онтологии
  → Source / Chunk без изменений
```

**Планируемая структура графа:**

```
Source ─[CONTAINS]→ Chunk

Requirement ─[REQUIRES_CONSTRAINT]→ Concept
    ↑                                    ↓
    │                              [REFERENCES]
    │                                    ↓
    └────────────────[CONTRADICTS]────── Contract
```

---

## 13. Итог: как сущности привязываются к чанкам

**Прямой привязки сущностей к чанкам в M1-M2 НЕТ.**

| Что | Как привязано |
|---|---|
| Source → Chunk | ребро `CONTAINS` (в графе) |
| Chunk → Vector | `chunk_id` (ключ в векторном хранилище) |
| Source → Entity | `source_ids` в свойствах Entity (список source_url) |
| Entity → Chunk | **нет прямой связи** |
| Entity → Entity | **нет рёбер** |

Сущности извлекаются из текста чанков (ExtractStage проходит по `ctx.chunks`), но в графе сущности не привязываются обратно к чанкам. Они знают только `source_url` (через `source_ids`), но не знают, в каких именно чанках они встретились.

**Почему так:** в M1-M2 ExtractStage — заглушка, которая просто вытаскивает слова. Реальные сущности из LLM (M3+) будут иметь `source_ids` (документы-источники), но привязка к конкретным чанкам (какие чанки содержат данную сущность) — требует отдельного механизма (например, инвертированный индекс «сущность → чанки» или хранение `chunk_ids` в свойствах Entity-узла). Кроме того, LLM-путь не реализован — задачи находятся в статусе `[ ]` в бандле `eval-graph-contribution-experiment`.