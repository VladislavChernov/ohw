# Процесс ингеста в Dz4 — нарезка, сущности, привязки

**Вид документа:** as-built снимок прототипа — как модуль устроен **сейчас**. Не спецификация:
нормы («как должно быть») живут в `docs/02_pipeline_and_normalizer.md`. По правилу 7
`analitic/ANALYTICAL_NOTES_RULES.md` этот файл отвечает на вопрос «от чего отталкиваться,
решая задачу», и его нельзя читать как описание без свежей отметки внизу шапки.

> Источники: `orchestrator.py` (все этапы), `chunker.py`, `document.py`, `domain_profile.it.yaml`, `docs/02_pipeline_and_normalizer.md`, `docs/01_ontology_and_domain_profile.md`, `CONCEPT.md §4`, `ADR-021`.

## Отметка о сверке

**Сверено с кодом:** всё содержимое, 2026-09-30, коммит `ac9f574`. Пройдены все ссылки на
строки (38 штук, каждая пересчитана по факту) и все утверждения о состоянии модуля. Пропущенный
номер раздела 3.3 остался: восстановить его нечем, и в перечень несоответствий он внесён.

Гард `tests/test_as_built_references.py` проверяет, что ссылки на строки **попадают в файл**, и
именно это он пропустил один раз: ссылки, записанные комментарием в код-блоке, а не в прозе
как `` `файл.py:N-M` ``. Обе формы теперь проверяются, и число тестов выросло с 47 до 58.
Смысловой дрейф гард не ловит по построению — он сверяет диапазон, а не содержание; ловит его
только пересверка, которую делал этот проход.

### 0.1 Что исправлено

В колонке «было» диапазоны приведены без обратных кавычек: это историческая запись о том, что
было неверно, а не утверждение о коде, и гард их проверять не должен.

| было | стало |
|---|---|
| orchestrator.py 37-40 — chunk_id, без домена в хеше | `orchestrator.py:313-316`, домен в хеше; поправлены оба места вызова и формула в §4 |
| chunker.py 54-77 / 80-126 | `chunker.py:61-84` / `chunker.py:87-133` |
| §3.2 — «4 стратегии», задокументированы две | дописаны c) `chunker.py:136-175` и d) `chunker.py:178-201`, плюс выбор стратегии `chunker.py:288-381` |
| §3.4 — `orchestrator.py:122-128` | `orchestrator.py:460-468`; добавлены четыре ветви выбора чанкера `orchestrator.py:450-459` |
| §5.1 «Текущая реализация (M1-M2 заглушка)», §5.2 «План M3 (LLM-извлечение)» | переписано: LLM — рабочий путь `orchestrator.py:601-885`, детерминированный — fallback `orchestrator.py:856-884` |
| §6 — `if not glossary_url: return`, только резолв | переписано: канонизация ключом, `tag_id`, подавление неоднозначных алиасов (L3-02), переписывание концов рёбер, схлопывание рёбер — `orchestrator.py:895-961`. Раннего выхода нет |
| §7 — группировка по `entity['canonical']` | переписано: ключ — `tag_id` или ключ идентичности, приоритет по `_origin_rank` — `orchestrator.py:991-1052` |
| §7 — «в M3+ косинусная дедупликация 0.92/0.75» | не план, а описание из `docs/plans/cosine-dedup.md`; косинус отклонён владельцем, единственный путь склейки — глоссарий |
| §8 «добавляет флаг `contract: True` всем сущностям» | тело стадии — `return None`, флага нет: `orchestrator.py:1055-1063` |
| §10.1 «`upsert` перед `_write`, выход по `created_new`» | такого в коде нет; реальный порядок — no-op, потом `_write`, потом `upsert`: `orchestrator.py:1274-1315` |
| §10.2 «Сущности одной меткой `Entity`», «только Source → CONTAINS → Chunk» | переписано: `ContextNode` (`orchestrator.py:69`), три стратегии записи, `extractor_version` от инструкции, node ID из ключа идентичности `orchestrator.py:323-324` |
| §10.3 — ветвление `_is_atomic_pair` с ручным `atomic_batch()` | вызовы `_write_atomic`/`_write_best_effort` как отдельные функции, компенсация внутри `_write_best_effort`: `orchestrator.py:1631-1674`, `1716`, `1759` |
| §11 — `node_types`, `edge_types`, `prompt_template` «не используются», LLM-путь не реализован | все три используются; таблица переписана, добавлено про мёртвые `extraction.temperature`/`max_tokens` |
| §12, §13 — сущности изолированы, привязки к чанкам нет | `chunk_ids` заполняются на COMMIT; §12.2 переписан в «чего нет» |
| §2, §3, §4, §6, §7, §8, §10.1, §10.3 — ссылки уехали на 300+ строк | все пересчитаны по факту: `orchestrator.py:414-429`, `430-470`, `471-488`, `887-985`, `986-1054`, `1055-1063`, `1224-1315`, `1631-1674` |

---

## 1. Обзор: 9 этапов ingestion pipeline

Пайплайн прогоняет документ последовательно через 9 стадий (файл `orchestrator.py:335-346`):

```python
STAGES = (
    'INGEST',    # чтение документа ридером → канонический Document
    'CHUNK',     # нарезка блоков на чанки
    'EMBED',     # эмбеддинг каждого чанка + chunk_id
    'EXTRACT',   # извлечение сущностей; LLM — рабочий путь, детерминированный — fallback
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

Файл: `orchestrator.py:414-429`

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

Файл: `orchestrator.py:430-470`, `chunker.py`

Вход: `ctx.document.blocks` — список блоков типа text/code/image.
Обрабатываются только блоки типов `text` и `code` (image пропускается).

### 3.1 chunk_id — идентификатор чанка

Составляется на этапе EMBED, но зависит от индекса чанка:

```python
# orchestrator.py:313-316
def _chunk_id(domain: str, source_url: str, index: int) -> str:
    digest = hashlib.sha256(f"{domain}:{source_url}:{index}".encode()).hexdigest()[:12]
    return f"chk:{digest}"
```

Пример: `chk:a1b2c3d4e5f6` — стабильный, детерминированный, зависит от домена, `source_url` и
порядкового номера чанка в документе. Домен в хеш входит не для красоты: `chunk_id` — граница
удаления, и узлы чанков разных доменов не должны совпадать (ADR-005, ADR-021).

### 3.2 Стратегии нарезки

В `chunker.py` реализовано 4 встроенные стратегии + плагины:

#### a) SlidingWindowChunker (дефолт, 512/64)

```python
# chunker.py:61-84
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
# chunker.py:87-133
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

#### c) LangChainChunker (опциональная зависимость)

```python
# chunker.py:136-175
class LangChainChunker(Chunker):
    SPLITTERS: ClassVar[dict[str, str]] = {
        "recursive": "RecursiveCharacterTextSplitter",
        "character": "CharacterTextSplitter",
        "token": "TokenTextSplitter",
    }
```

- Разделитель выбирается из трёх: `recursive` (дефолт), `character`, `token`
- Класс импортируется **лениво**, при первом обращении; если `langchain` не установлен —
  fail-fast с внятной ошибкой, а не `ImportError` на старте сервиса
- `chunk()` только делегирует `self._client.split_text(text)`

#### d) LlamaIndexChunker (опциональная зависимость)

Парсер выбирается из двух: `sentence` (`SentenceSplitter`) и `semantic`
(`SemanticSplitterNodeParser`) — `chunker.py:178-201`. Импорт ленивый, ошибка та же.

#### Выбор стратегии

Стратегия приходит не из кода, а из конфигурации: `INGEST_CHUNKER`,
`INGEST_CHUNK_SIZE`, `INGEST_CHUNK_OVERLAP`, `INGEST_LANGCHAIN_SPLITTER`,
`INGEST_LLAMAINDEX_PARSER`, плюс одноимённые ключи в секции профиля — сборка на
`chunker.py:288-381`. Значения по умолчанию: `DEFAULT_CHUNK_SIZE = 512`,
`DEFAULT_CHUNK_OVERLAP = 64` (`chunker.py:29-30`).

**Оговорка о номере раздела.** Между 3.2 и 3.4 нет 3.3. Раздел был ли вообще — по
закоммиченной версии установить нельзя, поэтому номер оставлен пропущенным, а не выдуман: выдуманный
номер выглядел бы как Contents, за которым ничего нет. В перечень несоответствий внесено.

### 3.4 code-блоки не рвутся

```python
# orchestrator.py:460-468
chunks: list[str] = []
for block in ctx.document.blocks:
    if block.type not in ("text", "code"):
        continue
    if not isinstance(block.data, str) or not block.data.strip():
        continue
    chunks += chunker.chunk(block.data)
ctx.chunks = chunks
ctx.chunks_meta = [{"index": i, "size_tokens": len(c)} for i, c in enumerate(chunks)]
```

Код-блоки чанкируются как отдельный текст — они не «размазываются» по соседним чанкам. Блоки
типа `image` пропускаются целиком.

**Выбор чанкера — четыре ветки, а не одна** (`orchestrator.py:450-459`): явно внедрённый
экземпляр (DI в тестах) → `build_chunker_from_profile` → `build_chunker()` → резолв per-job
через `build_chunker_for(ctx.domain)`. Precedence внутри последней: env > профиль домена >
namespaces > дефолты.

### 3.5 Результат CHUNK этапа

```python
ctx.chunks = ['chunk1 text...', 'chunk2 text...', ...]
ctx.chunks_meta = [{'index': 0, 'size_tokens': len('chunk1 text...')}, ...]
```

**`size_tokens` — имя врёт: там `len(c)`, то есть число символов, а не токенов.** Считать
токены на этом этапе нечем, эмбеддер появится только в EMBED. Поле не переименовано, потому
что его читает рендеринг отчётов, но при любой работе с ним это надо помнить.

**`chunk_id` на этом этапе ещё нет** — его проставляет EMBED (`orchestrator.py:487`). То есть
до стадии EMBER список чанков непригоден для оси связывания.

Из Domain Profile (`domain_profile.it.yaml`, секция `chunking`, строка 128):

```yaml
chunking:
  strategy: "sliding_window"
  chunk_size: 512
  overlap: 64
```

---

## 4. EMBED — эмбеддинги и chunk_id

Файл: `orchestrator.py:471-488`

```python
class EmbedStage(Stage):
    name = 'EMBED'

    def run(self, ctx: PipelineContext) -> None:
        for meta in ctx.chunks_meta:
            chunk = ctx.chunks[meta['index']]
            meta['embedding'] = self._embedder.embed(chunk, ctx.domain)
            meta['chunk_id'] = _chunk_id(ctx.domain, ctx.source_url, meta['index'])
```

- Для каждого чанка вызывается `Embedder.embed(text, domain) → list[float]`
- На M1-M2: `DeterministicEmbedder(dim=8)` — стабильный хэш-вектор, без GPU
- На M3: bge-m3 (1024 dim) через Embeddings Service :8004
- chunk_id вычисляется: `chk:<sha256(domain:source_url:index)[:12]>` — с доменом в хеше

**chunk_id — это ключевое звено между графовой и векторной осями.** Он записывается:
- как `node_id` узла `Chunk` в графовой оси
- как `chunk_id` в записи векторного хранилища

---

## 5. EXTRACT — извлечение сущностей

Файл: `orchestrator.py:601-885`

### 5.1 Рабочий путь — LLM

```python
# orchestrator.py:698-699
def _extract_llm(self, ctx: PipelineContext) -> None:
```

Это не план и не заглушка, а основной путь. Он:

- собирает инструкцию из профиля — схемная часть генерируется из `ontology.node_types` и
  `ontology.edge_types` (`prompt_renderer.render_schema_block`), метод добавляется из
  `METHOD_BLOCK`, проза профиля дописывается последней;
- считает идентичность извлечения от **содержимого** собранной инструкции
  (`extraction_identity`, `prompt_renderer.py:177-188`), а не от ручного `prompt_template.id`;
- вызывает адаптер на каждый чанк, разбирает JSON-объект, принимает списки сущностей по типам
  из профиля и `relationships`/`links`;
- проверяет связи: оба конца должны быть среди имён, объявленных в этом же ответе
  (`_validate_edges`, `orchestrator.py:833-854`); при нарушении — `ExtractionModelError`;
- проставляет `origin: "ai"`, `source_ids` и `chunk_ids` на каждое ребро.

Что из этого ещё не сделано и зафиксировано как открытое: неизвестный конец связи должен стать
отложенной ссылкой, а не отказом (ADR-037, случай 3, не реализован).

### 5.2 Детерминированный fallback

```python
# orchestrator.py:856-884
def _extract_deterministic(self, ctx: PipelineContext) -> None:
```

Словарь слов длиной от 5 букв, плоские сущности без типов, без рёбер. Нужен для fake/inmemory
режимов и тестов, где LLM недоступен, и включается, когда LLM выключен или помечен `is_fake`.

**Ограничения, из-за которых детерминированный путь не может быть основным:** он не знает про
онтологию, не создаёт типизированные узлы и не извлекает связей.

Дефолты профиля в `domain_profile.it.yaml` (`temperature: 0.1`, `max_tokens: 4096`) в коде
**не читаются**: действующие числа живут в блоке `llm` и берутся оттуда при паспортизации
извлечения (`_extraction_passport`, `orchestrator.py:555-598`). Это зафиксировано как
расхождение профиля и кода.

---

## 6. NORMALIZE — канонизация, алиасы и переписывание концов рёбер

Файл: `orchestrator.py:887-985`

```python
# orchestrator.py:895-961, сокращённо; порядок шагов значим
def run(self, ctx: PipelineContext) -> None:
    mapping: dict[str, str] = {}
    canonical_ids: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for entity in ctx.entities:
        name = str(entity.get("name") or entity.get("canonical") or "")
        canonical = str(entity.get("canonical") or entity.get("canonical_name") or name)
        if self._glossary_url:
            canonical = self._resolve(name or canonical, ctx.domain)
        entity["canonical"] = _identity_key(canonical)      # не сырое имя, а ключ
        canonical_key = _identity_key(canonical)
        entity["tag_id"] = _context_node_id(ctx.domain, entity)
        for value in (name, canonical, entity.get("canonical_name"), entity.get("tag_id")):
            ...
            previous = mapping.get(key)
            if previous is None:
                mapping[key] = entity_id
            elif previous != entity_id:
                mapping[key] = ""
                ambiguous_aliases.add(key)      # L3-02: не молча
    ctx.ambiguous_aliases = len(ambiguous_aliases)
    for edge in ctx.entity_edges:                          # концы рёбер переписываются
        edge["from"] = mapping.get(_identity_key(source)) or source
        edge["to"] = mapping.get(_identity_key(target)) or target
    unique_edges = {}                                      # рёбра схлопываются
    ctx.entity_edges = list(unique_edges.values())
```

Здесь четыре вещи, которых нет в прежней записи этого файла, и каждая важна:

1. **`canonical` — это ключ идентичности, а не имя из глоссария** (`_identity_key(canonical)`,
   `orchestrator.py:905`). Сырое имя живёт в `name`/`canonical_name`/`variants`.
2. **Неоднозначные алиасы не объединяются молча** (L3-02, ADR-031 §4). Если один алиас
   претендует на две разные ноды, отображение для него обнуляется и ключ попадает в
   `ambiguous_aliases`; число попадает в `ctx.ambiguous_aliases` и пишется в лог. Смысл
   counters'а: без него нельзя отличить документ без неоднозначности от документа, где их
   были десятки и молча выбросили, а `UNIQUE`-constraint этого не даёт — он умеет только
   слить или отвергнуть.
3. **Концы рёбер переписываются** через отображение (`orchestrator.py:942-948`). Рёбра,
   у которых уже проставлены `from_id`/`to_id`, не трогаются.
4. **Рёбра схлопываются по тройке `(source, target, kind)`** (`orchestrator.py:949-961`).

**Раннего выхода при пустом `glossary_url` нет.** Прежняя запись показывала
`if not self._glossary_url: return`, и это неверно: без глоссария стадия всё равно
канонизирует, назначает `tag_id`, строит отображение и дедуплицирует рёбра — пропускает
только сам вызов `_resolve`. Ошибка была существенная, потому что выглядела как «стадия
целиком отключается без глоссария».

Запрос к глоссарию: POST `/api/v1/glossary/resolve`, тело `{"term", "domain"}`, заголовок
`X-API-Key` из `AUTH_API_KEY`/`GRAPH_AUTH_API_KEY`, таймаут 2 с. `except` ловит
`OSError, TimeoutError, JSONDecodeError, ValueError` и возвращает исходный термин
(`orchestrator.py:963-983`).

---

## 7. DEDUP — слияние сущностей по нормализованному ключу

Файл: `orchestrator.py:986-1054`

```python
# orchestrator.py:991-1052, сокращённо
def run(self, ctx: PipelineContext) -> None:
    canonical_map: dict[str, dict[str, Any]] = {}
    for entity in ctx.entities:
        key = str(entity.get("tag_id") or _identity_key(entity.get("canonical") or entity.get("name") or ""))
        current = canonical_map.get(key)
        if current is None:
            current = dict(entity)
            current["sources"] = _entity_sources(entity)
            current["source_ids"] = list(current["sources"])
            current["chunk_ids"] = _entity_chunk_ids(entity)
            current["variants"] = _entity_variants(entity)
            canonical_map[key] = current
            continue
        # слияние: types, sources, chunk_ids, variants — объединением без повторов
        # ...
        if _origin_rank(entity.get("origin")) >= _origin_rank(current.get("origin")):
            for key_name in ("canonical", "canonical_name", "name", "origin", "confidence"):
                if key_name in entity:
                    current[key_name] = entity[key_name]
    ctx.entities = list(canonical_map.values())
    edges = {}                                  # рёбра по тройке (source, target, kind)
    ctx.entity_edges = list(edges.values())
```

**Логика:**
- Группирует сущности по `tag_id`, а при его отсутствии — по ключу идентичности `canonical`
- Ключ группы — **уже нормализованный**, потому что NORMALIZE записала в `canonical` ключ, а не имя
- При слиянии объединяются `types`, `sources`, `chunk_ids`, `variants` без повторов
- **Приоритет при конфликте** задаёт `_origin_rank`: поля `canonical`, `canonical_name`, `name`,
  `origin`, `confidence` перезаписываются, только если входящая запись не слабее текущей
  (`orchestrator.py:1021-1026`). То есть ручная разметка не затирается разобранной
- `id`, `description`, `category` дописываются, только если их ещё нет (`orchestrator.py:1032-1034`)
- Рёбра схлопываются по тройке `(source, target, kind)` (`orchestrator.py:1040-1052`)

**Чего стадия не делает.** Никакой косинусной дедупликации: `cosine ≥ 0.92` и
`0.75–0.92` из прежней записи этого файла — не реализованный план, а описание из
`docs/plans/cosine-dedup.md`. Косинусная дедупликация отклонена владельцем, единственный
разрешённый путь склейки — глоссарий (ADR-037). Второе следствие: `DEDUP` не снимает
петли и взаимные пары, потому что ключ ребра — тройка, а петля `A→A` и пара `A→B`/`B→A`
в ней различаются.


---

## 8. CONTRACT — заглушка иерархии

Файл: `orchestrator.py:1055-1063`

```python
class ContractStage(Stage):
    """CONTRACT: иерархия контрактов (M1 - заглушка, иерархия не склеивается)."""

    name = 'CONTRACT'

    def run(self, ctx: PipelineContext) -> None:
        return None
```

**Прежняя запись в этом файле утверждала, что стадия добавляет всем сущностям флаг
`contract: True`. Это неверно: тело стадии — `return None`, она ничего не делает.** Склейка
иерархии Contract-узлов по рёбрам EXTENDS/REFERENCES не реализована, и объявление вида связи
`REFERENCES` в `ontology.edge_types` на извлечение не влияет.

---

## 10. COMMIT — запись в граф и вектор

Файл: `orchestrator.py:1224-1315` (стадия), `orchestrator.py:1381-1674` (запись)

Это самый сложный этап. Он записывает:

### 10.1 DocumentRegistry (идемпотентность)

```python
# orchestrator.py:1274-1315 (сокращённо; порядок шагов значим)
def run(self, ctx: PipelineContext) -> None:
    if ctx.noop:
        return
    with self._registry.domain_lock(doc.domain):
        self._run_locked(ctx)

def _run_locked(self, ctx: PipelineContext) -> None:
    with self._registry.source_lock(doc.domain, doc.source_url):
        current = self._registry.latest_active(doc.domain, doc.source_url)
        if current and current["content_hash"] == doc.content_hash and not (
            ctx.tags or ctx.links or ctx.metadata
        ):
            ctx.commit_applied = True
            return                      # no-op: содержимое не изменилось
        ctx.metadata["revision"] = self._registry.data_revision_after(doc)
        ...
        try:
            self._write(doc, ctx)
        except CommitStageError as exc:
            if exc.compensated:
                self._registry.soft_delete(doc.domain, doc.source_url)
            raise
        except ValueError:
            raise
        except Exception as exc:
            if not self._graph_optional or self._graph_store is None:
                raise
            ctx.graph_projection_status = "degraded"
            self._write_vector_only(doc, ctx)
        result = self._registry.upsert(doc)
        ctx.registry_result = result
        ctx.commit_applied = True
```

Порядок именно такой, и он отличается от прежней записи в этом файле: **сначала проверка
no-op, потом `_write`, и только потом `upsert` в реестр.** Прежняя версия показывала
`upsert` перед `_write` и выход по `created_new`, чего в коде нет.

Два уровня блокировок (`domain_lock`, затем `source_lock`) и три ветки обработки сбоя записи
(`CommitStageError` с компенсацией → soft delete; `ValueError` → наружу; прочее → `degraded` и
откат на вектор, если граф опционален) описаны здесь потому, что каждая из них определяет, что
останется в графе после неудачной загрузки.

### 10.2 Что пишется в граф: узлы и рёбра

Файл: `orchestrator.py:1381-1674`

**Метка сущности — `ContextNode`, а не `Entity`.** Прежняя запись в этом файле утверждала
«все сущности одной меткой `Entity` и без типов»; это superseded 2026-09-25, см. шапку
`Ingest/graph_vector_interaction.md`. Сейчас константа `CONTEXT_NODE_LABEL = "ContextNode"`
(`orchestrator.py:69`), и все списки сущностей из профиля отображаются в неё
(`orchestrator.py:708-716`) — то есть тип из онтологии в метку узла **не попадает**.

```python
# orchestrator.py:69
CONTEXT_NODE_LABEL = "ContextNode"

# orchestrator.py:323-324
def _entity_node_id(domain: str, canonical_name: str) -> str:
    return f"ent:{domain}:{_identity_key(canonical_name)}"
```

**Идентичность узла выводится из ключа идентичности.** Отсюда следует практическое следствие,
которое стоит держать в голове при любой работе с границами: `DedupStage` и `DEDUP_STAGE` дают
разные ключи (`dedupstage` и `dedup_stage`), а значит **два разных `node_id` и два узла на одну
сущность**. Склейка произойдёт только если alias лежит в глоссарии. Подробно —
`Ingest/analitic/node_identity_who_owns_tag_id.md`.

**`extractor_version` — не константа.** `EXTRACTOR_VERSION = "deterministic:v1"`
(`orchestrator.py:64`) проставляется только детерминированным путём; для LLM-извлечения версия
считается от содержимого инструкции (`extraction_identity`) и имеет вид
`llm:<профиль>@<версия>:<отпечаток инструкции>`.

**Три стратегии записи**, выбор — по наличию хранилища и флагу «граф опционален»:

| условие | путь | где |
|---|---|---|
| граф есть, вектор есть | `_write` — основной | `orchestrator.py:1381` |
| графа нет | `_write_vector_only` — векторная базовая линия | `orchestrator.py:1675` |
| запись графа упала, а граф опционален | `degraded` + откат на `_write_vector_only` | `orchestrator.py:1307-1312` |

Плюс пара «атомарно или best_effort» на уровне адаптера, выбор по capability и `engine_key()`
(ADR-024, `orchestrator.py:1716` и `orchestrator.py:1759`).

**Про no-op.** `try_noop` / проверка в `_run_locked` (`orchestrator.py:1286-1291`): если
`content_hash` совпал с последней активной версией и нет ручных тегов, связей и метаданных,
запись не выполняется вовсе. Следствие, важное для понимания цены ошибки: повторная загрузка
того же файла **не перезапускает экстракцию**.

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

Файл: `orchestrator.py:1631-1674`

```python
# orchestrator.py:1631-1674
stale_chunks = list(
    dict.fromkeys([
        *graph.list_chunk_ids_of_source(source_id),
        *vector.list_chunk_ids_of_source(source_url, domain),
    ])
)
# ADR-028: детерминированный порядок — защита от deadlock-циклов
nodes.sort(key=lambda n: n["node_id"])
edges.sort(key=lambda e: (e["from_id"], e["to_id"], e["type"]))

if _is_atomic_pair(graph, vector):
    _with_commit_retry(stores, lambda: self._write_atomic(...))
    ctx.graph_projection_status = "committed"
    return
self._write_best_effort(..., stale_chunks, written_chunk_ids, ...)
ctx.graph_projection_status = "committed"
```

**Атомарная пара** (`_is_atomic_pair`): обе оси сообщают `consistency_capability() == 'atomic'`
и `engine_key()` совпадает. Тогда запись идёт через `_with_commit_retry` и `_write_atomic`
(`orchestrator.py:1716`), где граф и вектор пишутся в одной транзакции движка — `atomic_batch()`
для Neo4j, `session.begin_transaction()` для драйвера.

**best_effort** (`orchestrator.py:1759`) — путь по умолчанию: ретраи и компенсация выполняются
по осям внутри самой функции, а не оборачивающим вызовом. Прежняя запись этого файла
показывала `_is_atomic_pair` как условие внутри `_write` с последующим `atomic_batch()` и
`_write_best_effort(...)` вручную — такой последовательности в коде нет, и по ней легко было бы
сделать вывод, что компенсация графа при сбое вектора происходит автоматически. Она происходит
внутри `_write_best_effort`, и это отдельный кусок логики, а не следствие ветвления.

**Проверка целостности перед записью** (`orchestrator.py:1620-1629`): сущность, ссылающаяся на
неизвестный `chunk_id`, поднимает `ValueError` — и этот случай в `_run_locked` не
компенсируется, а поднимается наружу.

**`stale_chunks` собирается с обеих осей** и дедуплицируется, то есть набор устаревших чанков
источника — объединение того, что знает граф, и того, что знает вектор.

---

## 11. Domain Profile и его влияние на конвейер

Файл: `domain_profile.it.yaml`

```yaml
ontology:
  node_types: [...]      # type, gloss, emitted — из них строится схема промпта
  edge_types: [...]      # from, to, type — отсюда берётся форма объекта связи
extraction:
  prompt_template:
    system: '...'
    user: '...'           # метод профиля; схема сюда не дублируется
  temperature: 0.1
  max_tokens: 4096
chunking: { strategy, chunk_size, overlap }
```

### Что реально читает код

| Секция профиля | Используется? | Где и как |
|---|---|---|
| `profile` | да | `domain` как параметр; `name`/`version` в паспорте извлечения |
| `ontology.node_types` | **да** | ключи JSON-списков ответа модели (`_plural_entity_key`), и же они генерируются в промпт: `prompt_renderer.render_schema_block` |
| `ontology.edge_types` | **да** | список разрешённых `kind` и направления — тоже в промпте; отсюда же берётся допустимость направления |
| `extraction.prompt_template` | **да** | `system` — системный промпт, `user` — метод поверх сгенерированной схемы |
| `extraction.temperature` / `max_tokens` | **нет** | объявлены, но не читаются; действующие числа живут в блоке `llm` и берутся оттуда при паспортизации |
| `chunking` | да | `build_chunker_from_profile`; precedence env > профиль > namespaces > дефолты |
| `validation.rules` | нет | в прототипе не проверяется |
| `canonicalization` | нет | NORMALIZE ходит только в глоссарий |
| `context_assembly`, `retrieval` | вне ингеста | читает retrieval |

Прежняя запись этого файла утверждала, что `node_types`, `edge_types` и `prompt_template` в
ингесте не используются, а LLM-путь не реализован. Это было верно для заглушки и перестало быть
верно вместе с `ExtractStage._extract_llm`; `edge_types` и `node_types` теперь влияют и на
промпт, и на форму ответа.

**Расхождение профиля и кода, зафиксированное как факт:** `extraction.temperature: 0.1` и
`extraction.max_tokens: 4096` в профиле не влияют ни на что. Это расхождение и было причиной
появления паспорта извлечения — он фиксирует, чем выполнялся разбор на самом деле.


---

## 12. Привязки сущностей — что сейчас

Прежняя запись этого файла описывала здесь заглушку: «сущности — изолированные узлы `Entity`,
между ними рёбер нет, привязки к чанкам нет». Это верно для детерминированного пути и неверно
для рабочего. Сейчас:

```
Документ → чанки → сущности (LLM или детерминированный путь) → узлы ContextNode

Граф после коммита:
  Source ─[CONTAINS]→ Chunk
  Chunk  ─[MENTIONS]→ ContextNode        (если задан ontology.chunk_entity_edge)
  ContextNode ─[kind]→ ContextNode       (рёбра из ответа модели)
  ContextNode.properties.chunk_ids       (привязка к чанкам)
```

`ontology.chunk_entity_edge` — связь `чанк → сущность`, которую строит система; модель о ней не
знает, и промпт это говорит прямо (`prompt_renderer.py:115-121`).

### 12.2 Чего нет и не планируется

Планировавшаяся структура с отдельными метками узлов не реализована:

```
Планировалось:  Requirement ─[REQUIRES_CONSTRAINT]→ Concept ─[REFERENCES]→ Contract
Фактически:     ContextNode  ─[REQUIRES_CONSTRAINT]→ ContextNode ─[REFERENCES]→ ContextNode
```

То есть **вид** связи из ответа модели сохраняется, а **тип** узла теряется. Планировавшаяся
схема с `CONTRADICTS` между требованиями в профиле объявлена, но потеряет смысл, как только
различать типы станет возможно: сейчас `CONTRADICTS` и `REQUIRES_CONSTRAINT` на графе не
различимы, если смотреть на метку узла, и различаются только по `kind` ребра.

Проверка реализуемости: `_context_node_id` и `CONTEXT_NODE_LABEL` — единственные места, где
метка ноды определяется, и обе константы. Чтобы тип дошёл до графа, нужно либо развести метки
по `ontology.node_types`, либо хранить тип в свойствах; решение не принято.

---

## 13. Привязка сущности к чанку и почему это важно для уборки

Сущности **имеют** привязку к чанкам — в свойствах узла, полем `chunk_ids`. Прежняя запись
этого файла утверждала обратное («прямой привязки нет», «нужен отдельный механизм»), и это было
верно только для детерминированного пути, где `chunk_ids` действительно не заполнялись.

| Что | Как привязано |
|---|---|
| Source → Chunk | ребро `CONTAINS` в графе |
| Chunk → вектор | `chunk_id` как ключ записи векторного хранилища |
| сущность → документ | `source_ids` / `sources` в свойствах узла |
| **сущность → чанк** | **`chunk_ids` в свойствах узла**, заполняется на COMMIT |
| сущность → сущность | рёбра из `ctx.entity_edges`; вид связи — из `kind` ответа модели |

**Почему `chunk_ids` важны для уборки.** Удаление выбирает рёбра-кандидаты по отсутствию опоры
на `chunk_ids`; ребро без этого поля считается структурным, и структурные уборка не трогает
(`docs/02` §4.5). Поэтому `chunk_ids` проставляются платформой, а не моделью: промптом это
запрещено, а на этапе записи поле заполняется всегда (`orchestrator.py:789-816`). Ровно это
и было причиной появления `origin: "ai"` у извлечённых рёбер.

**Чего в графе нет.** Тип из онтологии в метку узла не попадает: все сущности — `ContextNode`
(§10.2). Планировавшаяся структура с отдельными `Requirement`/`Concept`/`Contract` и рёбрами по
`edge_types` не реализована, и объявление `REFERENCES` в профиле на извлечение не влияет.

---

## 14. Что стоит знать, прежде чем менять модуль

Собрано из расхождений, найденных при сверке 2026-09-30. Каждое проверено по коду.

1. **`contract` не ставится.** `ContractStage` — `return None` (§8). Поле `contract: True` в
   прежней версии этого файла не существует.
2. **Тип ноды не доходит до метки.** `ContextNode` на всё (§10.2), так что онтологию по графу
   не восстановить.
3. **`extraction.temperature` и `max_tokens` в профиле мертвы** (§11). Действующие значения
   берутся из блока `llm`.
4. **`size_tokens` — это `len()`** символов, не токенов (§3.5).
5. **`canonical` — ключ идентичности, а не имя** (§6). Имя лежит в `name`/`canonical_name`.
6. **Рёбра схлопываются дважды** — в NORMALIZE и в DEDUP, обе по тройке
   `(source, target, kind)` (§6, §7). Ни одна из двух стадий не снимает петли и взаимные пары.
7. **Повторная загрузка того же `content_hash` — no-op** (§10.1), то есть экстракция не
   перезапускается. Простая перезагрузка файла не чинит неудачное извлечение.
8. **Неизвестный конец связи сносит LLM-слой документа целиком** (§5.1) — `_validate_edges`
   поднимает `ExtractionModelError`, а при `optional_failure` `_degrade_after_extraction_failure`
   чистит `ctx.entities` и `ctx.entity_edges`. Правка этого — ADR-037, случай 3, не сделана.
9. **Пустой глоссарий не отключает NORMALIZE** (§6): отключается только сетевой вызов.
