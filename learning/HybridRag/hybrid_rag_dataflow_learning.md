# Гибридный RAG: vector-first retrieval и лёгкий context graph — карта изучения (методичка)

Предметная область, которая всплыла при аналитике ингеста и хранения Dz4
(`Ingest/analitic/analysis_ingest_storage_industry_GLM.md`). Ядро — понимание того,
**как vector-поиск находит семантические seeds, как лёгкий граф дополняет их явными
связями и как доказательства собираются в один ограниченный контекст**.

**Как читать:** разделы ниже идут от «обязательного минимума» к «углублённо».
В каждом — 1-2 конкретных внешних источника и чек-пойнт «понял» для самопроверки.

> **Статус Dz4 на 2026-09-25.** Vector-only baseline с metadata — **Runtime**;
> graph — отдельный **Experiment**. Primitive ingest не требует профиля, LLM, тегов или
> связей. Graph projection может строиться inline после vector commit или отдельным offline
> replay; пока она не готова, baseline не меняется. Схемы ниже — учебная Target-модель.
>
> Тяжёлые ontology-driven графы приведены только как краткий справочник для сравнения;
> они не являются целевой архитектурой Dz4.

---

## 0. Ментальная модель: сначала найти, затем расширить

Лёгкий гибрид разделяет работу не на две равноправные базы, а на baseline и optional experiment:

| Контур | Пример вопроса | Кто отвечает | Что получает |
|---|---|---|---|
| **Vector baseline** | «где описан запуск пайплайна?» | Векторный поиск | Текстовые chunks, sources, provenance и metadata |
| **Graph experiment** | «какие соседние сущности связаны с найденным контекстом?» | Готовая context graph projection | Ограниченный путь, связанные nodes и evidence chunk IDs |
| **Assembly** | «что достаточно включить в ответ?» | Дедупликация, reranker, budget | Один проверяемый контекст с provenance |

Главная идея: **граф не запускает отдельный соревнующийся поиск**. Он получает seeds
из vector metadata и только расширяет уже найденную окрестность. Поэтому vector-only
остаётся корректным baseline, а вклад графа можно измерить отдельно.

```text
 Запрос пользователя: "что зависит от OAuth?"
         │
         ▼
 [1] Embedder: query → vector
         │
         ▼
 [2] Vector search: semantic seeds
     {chunk_id, text, score, context_ids, ...}
         │
         ├── baseline: no graph ────────────────────────┐
         │                                             │
         ▼                                             │
 [3] Graph experiment (optional, ready projection):  │
      expand(context_ids, max_depth, max_fanout, max_nodes) │
     → paths + related context IDs                    │
         │                                             │
         ▼                                             │
 [4] Hydration + dedup: вернуть текст evidence из     │
     vector/KV, не дублируя его в graph                │
         │                                             │
         └──────────────────────┬──────────────────────┘
                                ▼
 [5] Rerank + bounded Context Assembly
                                │
                                ▼
 [6] LLM: prompt (system + evidence + question)
                                │
                                ▼
 Ответ + sources + retrieval trace
```

`context_ids` — намеренный мост между baseline и experiment, а не обязательная зависимость:
поиск сначала находит текстовые seeds, а готовый graph projection объясняет, какие явные связи
стоит проверить. Projection может быть построена inline после vector commit или offline replay.
Если графа нет, он отстал или вернул только уже найденные chunks, результат не хуже
vector-only baseline.

**Теория:**
- **Anthropic: Building effective agents / retrieval** — https://www.anthropic.com/engineering —
  инженерная рамка разделения retrieval на этапы.
- **NVIDIA: What is Retrieval-Augmented Generation** — https://www.nvidia.com/en-us/glossary/retrieval-augmented-generation/ —
  базовое различие retrieval и generation.

**Чек-пойнт «понял»:** нарисуй путь запроса до ответа. Что произойдёт, если удалить
блок `[3]`, и почему граф здесь не является вторым независимым retriever?

---

## 1. Vector seeds: где находится тело evidence

### 1.1 Payload-канон

Стандартная запись хранит текст и связь с контекстным графом рядом с вектором:

```text
point {
  id:      "chk:a1b2c3d4",
  vector:  [0.12, -0.83, ...],
  metadata: {
    text:         "Вызовите метод execute()...",
    source_id:    "src:docs-42",
    source_url:   "https://docs.example/arch",
    domain:       "it",
    revision:     "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    index:        0,
    context_ids: ["it:tag:oauth", "it:concept:token-refresh"]
  }
}
```

Правило: **payload содержит текст, provenance и IDs, по которым можно безопасно расширить
результат**. Векторная запись отвечает за тело evidence; context graph — за связи и
provenance, но не за вторую копию текста.

`context_ids` не превращают vector metadata в ontology. Это адреса известных graph nodes;
их может не быть, и ingest не обязан их создавать.

### 1.2 Что происходит при запросе

```text
1. query = "что зависит от OAuth?"
2. embedder.embed(query) → q_vec
3. vector_search(q_vec, top_k=20)
4. result = [{chunk_id, text, source_url, score, context_ids}, ...]
5. выбрать initial seeds для reranker и graph expansion
```

Векторный поиск находит смысл, а не формальные связи. Запрос «аутентификация» может
найти чанк про OAuth, где слово «аутентификация» не встречается. Обратная сторона:
он не знает, что чанк A зависит от чанка B, если зависимость не выражена в тексте
и не сохранена в `context_ids`/graph links.

### 1.3 Оценка близости

Стандарт — косинусная близость:

```text
cos(q, v) = (q·v) / (|q| · |v|) ∈ [-1, 1]
```

- 1.0 — одинаковое направление, смысл совпадает;
- 0 — векторы ортогональны;
- score не является процентом совпадения: его сравнивают внутри одного поиска.

**Теория:**
- **Qdrant concepts: Embeddings** — https://qdrant.tech/articles/what-are-embeddings/ —
  эмбеддинги и разные меры расстояния.
- **Jina AI: Late Chunking** — https://jina.ai/news/late-chunking-in-long-context-embedding-models/ —
  альтернативы нарезке чанков; в Dz4 отложены.

**Чек-пойнт «понял»:** почему vector search находит OAuth-чанк по запросу
«аутентификация», но не находит неявную зависимость между двумя chunks? Что должно
добавить vector metadata, чтобы эта зависимость стала проверяемой?

---

## 2. Лёгкий context graph: optional offline experiment

### 2.1 Что лежит в графе

Граф хранит **ids, relationships и provenance**, а не corpus-текст:

```text
(:Source {source_id, domain, revision})
  -[:HAS_CHUNK]->
(:Chunk {chunk_id, source_id, index})
  -[:MENTIONS]->
(:ContextNode {tag_id, canonical_name, aliases, origin, confidence, source_ids})

ContextEdge {
  edge_id, from_id, to_id, kind: parent | related | mentions,
  origin, confidence, source_ids
}
```

`ContextNode` здесь — технический контейнер, а не класс доменной онтологии. Он может
представлять тег, понятие, пользовательский термин или другую разрешённую смысловую
сущность. Обязательной типизации `Requirement|Concept|Contract` и whitelist типов рёбер нет.

Для tag identity используются:

- стабильный `tag_id` в пределах domain;
- `canonical_name` как preferred label, но не unique constraint;
- `aliases` для синонимов и других языков;
- optional glossary/AI suggestions для кандидатов;
- неоднозначные варианты не объединяются молча.

Links могут прийти вручную или от AI. Primitive ingest допустим вообще без них.

Projection строится двумя способами: inline best-effort после vector commit или отдельным
идемпотентным offline replay корпуса. Offline job может backfill-ить `context_ids` в vector
metadata и фиксировать readiness/revision; до этого baseline остаётся полностью рабочим.

### 2.2 Как работает expansion

```text
seed_context_ids = union(vector_results[].context_ids)
paths, nodes = graph.expand(
    context_ids=seed_context_ids,
    direction=parent,
    max_depth=bounded_depth,
    max_fanout=bounded_fanout,
    max_nodes=bounded_nodes,
)
evidence = hydrate(paths.nodes, paths.evidence_chunk_ids)
candidates = dedupe(vector_results + evidence)
```

Expansion получает seeds, а не пытается заново решить запрос по терминам. Он обязан:

1. фильтровать по active domain/revision;
2. ограничивать hops, nodes, edges и итоговый budget;
3. возвращать путь, по которому найден кандидат;
4. отделять уже найденные vector seeds от новых graph-only evidence;
5. сохранять `origin`/`axis`, иначе вклад графа нельзя измерить.

Текст новых evidence chunks извлекается из vector payload или KV. Если chunk ID есть
в пути, но текст недоступен, путь остаётся диагностическим артефактом, но не источником
для ответа.

Пример для запроса «что зависит от OAuth?»:

```text
1. Vector search находит OAuth-документ и OAuth-тест.
2. Их context_ids дают seeds: OAuth, TokenRefresh, две связанные главы.
3. Expansion находит главу про refresh token и её evidence chunk.
4. Дедупликация убирает OAuth-документ, уже найденный вектором.
5. Reranker объединяет прямой текстовый hit и graph-only hit по общему запросу.
```

**Теория:**
- **Neo4j Graph Data Modeling** — https://neo4j.com/docs/get-started/graph-data-modeling/ —
  как отделять лёгкие pointers от тяжёлых узлов.
- **Neo4j variable-length paths** — https://neo4j.com/docs/cypher-manual/current/patterns/variable-length-patterns/ —
  почему число hops должно быть ограничено.

**Чек-пойнт «понял»:** дан результат vector search с `context_ids=[A, B]` и путь
`B -[kind=related]-> C -[kind=related]-> D`. Какие IDs становятся seeds, какой фрагмент может дать
новое evidence и где обязательно записать `origin=graph`?

### 2.3 Справочник: когда граф становится тяжёлым

Тяжёлый ontology-driven GraphRAG — осознанный другой класс системы:

| Признак | Лёгкий context graph в Dz4 | Heavy graph |
|---|---|---|
| Содержание | Опциональные tags и связи вокруг найденных chunks | Объёмная entity/relation extraction корпуса |
| Identity | Стабильный tag/node ID + aliases | Строгая ontology, schema и сложный entity resolution |
| Ingest | Primitive ingest без LLM | Обычно LLM-экстракция и reconciliation |
| Retrieval | Bounded expansion от vector seeds | Multi-hop, communities, global summaries |
| Цена | Небольшая дополнительная стадия | RAM/LLM latency, reindex и сложная эксплуатация |

Microsoft GraphRAG и другие heavy-подходы полезны для global-вопросов и сложной
транзитивной модели, но эти задачи не являются целью текущего Dz4. Если запрос не требует
явной иерархии или трассировки связей, heavy ontology не покупает автоматически лучший
vector retrieval — она увеличивает стоимость ingest и усложняет provenance.

**Теория:**
- **Microsoft GraphRAG** — https://microsoft.github.io/graphrag/ — канонический пример
  heavy pipeline с извлечением сущностей и community summaries.

### 2.4 Справочник: parallel-axis model

**Parallel-axis model** — vector search и graph search запускаются независимо, каждый
приносит свой список кандидатов, а fusion объединяет результаты позже:

```text
query → vector candidates ─┐
                           ├→ fusion/rerank → context
query → graph candidates ──┘
```

**Где это разумно:**

- graph search имеет собственный сильный якорь: entity IDs, exact relations или global index;
- источники принципиально разные, и один должен работать даже при недоступности другого;
- нужно независимо масштабировать и измерять два retrieval-контура;
- fusion/reranker умеет корректно объединить разные score scales и provenance.

**Почему это плохо для текущего Dz4:**

- выполняются два полных ranking-прохода вместо одного дешёвого baseline и bounded expansion;
- graph-кандидат может не быть связан с найденным текстом, поэтому его полезность трудно объяснить;
- fusion создаёт конкуренцию за один token budget и часто дублирует один chunk;
- ошибка entity matching или устаревшего graph индекса маскируется большим vector-результатом;
- paired eval должен одновременно контролировать оба branch path, иначе прирост смешивается с fusion/reranker.

Поэтому parallel-axis остаётся полезным учебным и экспериментальным вариантом, но не
является target-потоком Dz4: сначала vector seeds, затем optional graph expansion.

**Теория:**
- **Qdrant: Hybrid Search** — https://qdrant.tech/articles/hybrid-search/ — fusion
  разных retrieval-выдач.
- **Neo4j Graph Data Modeling** — https://neo4j.com/docs/get-started/graph-data-modeling/ —
  когда отдельный graph-контур действительно оправдан.

**Чек-пойнт «понял»:** предложи запрос, для которого parallel-axis лучше vector-first.
Затем назови одну метрику, которая докажет преимущество, и одну причину, по которой
результат может оказаться ложным.

---

## 3. Fusion и Context Assembly

Graph experiment expansion возвращает кандидатов, но не решает, что попадёт в prompt. Задача —
собрать минимальный достаточный контекст без потери provenance и без двойного текста.

### 3.1 Правила сборки

1. Vector seeds дают основной grounding и сохраняют свой `origin`.
2. Graph-only evidence добавляется с коротким путём: `A -[relation]-> B`.
3. Повторный chunk удаляется; при конфликте сохраняются provenance обоих путей.
4. Кандидаты rerank-ятся по query + chunk, а не только по исходному cosine score.
5. Path budget, число evidence chunks и token budget ограничены независимо.
6. Ни vector seed, ни graph path не имеет абсолютного приоритета «никогда не вытесняться».

### 3.2 Итоговый промпт

```text
SYSTEM: Ты ассистент. Отвечай только по context. Указывай источники.

USER:
## Vector evidence
[0.82] https://docs.example/oauth: OAuth используется token endpoint.

## Related context
OAuth -[related: uses]-> TokenRefresh
[0.74] https://docs.example/refresh: Refresh token заменяет истёкший access token.

## Question
Что зависит от OAuth?

## Sources
- https://docs.example/oauth
- https://docs.example/refresh
```

Структурная строка помогает увидеть, **почему** graph evidence добавлен, но не заменяет
текст цитаты. Если budget мал, путь сокращается или удаляется первым; evidence chunk
может быть сохранён, если он сам релевантен и имеет provenance.

**Чек-пойнт «понял»:** почему нельзя считать, что graph path всегда ценнее vector
текста, и какие поля нужны, чтобы убрать path, сохранив полезное evidence?

---

## 4. Reranker: где смешиваются кандидаты

Векторный поиск — recall-этап baseline. В graph experiment после optional expansion reranker
получает **объединение** прямых vector hits и graph-only evidence:

```text
vector_search(top_k=20)
  + graph_expansion(context_ids)
  → hydrate + dedupe
  → reranker.rerank(query, candidates)
  → bounded top-k context
```

Почему не сразу top-5 по cosine: разные шкалы и corpus collection делают исходный
vector score хорошим кандидатным фильтром, но ненадёжным объяснением релевантности
текста, найденного через связь. Reranker сравнивает query и каждый chunk уже после
deduplication.

Если reranker отсутствует, допустим простой fusion по stage/score, но выбор должен быть
зафиксирован в manifest: иначе нельзя отличить вклад graph expansion от смены ранжирования.

**Теория:**
- **Hugging Face: Cross-Encoders** — https://sbert.net/examples/applications/cross-encoder/README.html —
  различие bi-encoder и cross-encoder.
- **Cohere: Reranking** — https://docs.cohere.com/docs/reranking —
  типичный reranking pipeline.

**Чек-пойнт «понял»:** какие данные должны попасть в reranker при включённом графе?
Почему graph path нельзя использовать как текст evidence вместо chunk payload?

---

## 5. Инварианты baseline и optional graph

Минимальные правила, без которых optional experiment превращается в тяжёлую систему:

1. **Primitive ingest валиден сам по себе.** Пустые tags/links не являются ошибкой.
2. **Vector-only baseline не зависит от graph availability.** Disabled, missing или stale graph
   возвращает тот же набор seeds, что и до подключения expansion.
3. **Projection readiness фиксируется отдельно.** Inline и offline builds не меняют baseline,
   пока experiment index не готов.
4. **Текст хранится один раз.** Graph содержит IDs, relationships и provenance.
5. **Фильтры одинаковы.** Domain/revision проверяются на vector results и graph paths.
6. **Expansion ограничен.** Бесконечный multi-hop и разрастание prompt запрещены.
7. **Origin сохраняется.** У каждого evidence item есть `vector` или `graph`, а у fused
   результата — пути получения.
8. **Ручные и AI links равноправны.** AI не является обязательным gatekeeper.

| Нарушение | Симптом | Почему плохо |
|---|---|---|
| Offline projection отстала | Graph links не соответствуют vector metadata | Эксперимент измеряет разные корпуса |
| Primitive ingest требует LLM enrichment | Документ не ингестится без необязательного сервиса | Ломается независимость baseline |
| Полный текст продублирован в graph nodes | RAM и reconciliation растут | Появляется второй источник истины |
| Expansion ищет заново по терминам запроса | Graph results не привязаны к seeds | Теряется причинность и парность eval |
| Ветка graph on/off меняет vector seeds | Нельзя attribute delta | Эксперимент меняет несколько факторов |
| `context_id` не разрешается | Evidence ID есть, текста нет | Ответ ссылается на непроверяемый объект |
| `canonical_name` используется как unique key | `OAuth` и `Быстрый OAuth` конфликтуют | Ошибки multilingual identity и merge |

Архитектурный тест: **добавление optional graph не должно менять vector-only результат,
если expansion пуст**. Второй тест: отключение graph adapter должно давать штатный
baseline, а не ошибку ingest или query.

---

## 6. Мини-словарь

| Термин | Значение |
|---|---|
| **chunk_id** | Стабильный ID текстового evidence |
| **payload/metadata** | Текст, provenance, score-related поля и `context_ids` векторной записи |
| **seed** | Vector hit, найденный до graph expansion |
| **context_id** | ID graph node, с которым связан chunk |
| **graph expansion** | Ограниченный обход от seed IDs с возвратом paths и evidence IDs |
| **origin/axis** | Как кандидат попал в результат: `vector` или `graph` |
| **hydration** | Получение текста evidence по ID из vector payload/KV |
| **Context Assembly** | Dedup, rerank и сборка кандидатов в ограниченный prompt |

---

## Координация с проектом

- Целевой контракт: `Dz4/openspec/changes/add-lightweight-context-graph/`.
- Primitive ingest и optional enrichment: `Dz4/docs/02_pipeline_and_normalizer.md`.
- Vector-first retrieval: `Dz4/docs/03_retriever.md`.
- Generic node/link contract и multilingual identity:
  `Dz4/docs/01_ontology_and_domain_profile.md`.
- Оценка vector-only против optional graph experiment: `Dz4/docs/test_plan.md`.
- Heavy GraphRAG дан только для сравнения; его ontology/entity pipeline в experiment не переносится.

---

## Упражнения для самопроверки (сквозные)

1. Vector search вернул chunks с `context_ids=[A, B]`; graph path содержит
   `A -[kind=related, relation=uses]-> C`, а текст chunk C уже находится в vector results. Что попадёт
   в итоговый набор, какие поля нужны для provenance и что будет считаться graph-only?
2. Почему score разных vector search нельзя сравнивать? Почему после graph expansion
   всё равно нужен общий reranker для прямых и новых кандидатов?
3. Спроектируй запрос, где лёгкий graph полезен, и запрос, где heavy GraphRAG был бы
   оправдан. Для каждого назови цену, которую нельзя списать на «умный поиск».
4. У graph path есть `evidence_chunk_id`, но hydration не нашла текст. Допустимо ли
   отправить LLM только path? Почему отсутствие текста должно быть явным degraded mode?
5. Сравни `canonical_name="OAuth"`, alias `«Быстрая авторизация»` и кандидат
   `«Auth»`. Как связать их без unique constraint и что делать при неоднозначности?
6. Спроектируй manual-only, AI-only и no-enrichment варианты одного документа. Для каждого
   покажи, почему ingest остаётся валидным и какой baseline получает пользователь.

---

## Живой пример в проекте

- Target workflow: `Dz4/openspec/changes/add-lightweight-context-graph/spec.md`.
- Payload и context IDs: `Dz4/docs/data_model.md`.
- Vector-first pipeline: `Dz4/prototype/src/graphrag_proto/retrieval/pipeline.py`.
- Самопроверка: отключение или пустой expansion не меняет vector-only seeds.
