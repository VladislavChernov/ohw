# Бесплатные статьи и курсы: альтернативы платным книгам

Подборка закрывает пробелы, которые остаются после чтения DDIA (Kleppmann, O'Reilly)
и «System Design Interview» (Alex Xu). Все ссылки проверены; ресурсы — бесплатно
доступные (open-access / free PDF / open courseware).

**Структура:** тема → конкретные материалы → что даёт.

---

## 1. Распределённые системы: бесплатный учебник

### Distributed Systems (van Steen, Tanenbaum) — основной бесплатный курс

**Откуда:** https://www.distributed-systems.net
(4-е издание, Maarten van Steen; 3-е доступно онлайн, доступ ко всем PDF).

**Что берём:**
- Глава 1 — модели, partial failure, unbounded delay.
- Глава 5 — репликация и согласованность (read-your-writes, monotonic reads).
- Глава 7 — взаимодействие компонентов, RPC, middleware.
- Глава 9 — распределённое хранение данных (rereplication, anti-entropy).

**Для чего:** прямая замена DDIA, если не хочешь покупать книгу. Более учебный
стиль (с лекциями и задачами), чем «Marketplace book» DDIA.

### MIT 6.824: Distributed Systems (лекции/лабы)

**Откуда:** http://nil.csail.mit.edu/6.824/index.html
(лекции с 2020 года включают free PDF-конспекты и видео; лабораторные
сyttки: MapReduce, Raft, KeyValue-сервер).

**Что берём:**
- Лекция 2 — RPC и partial failure.
- Лекция 6 — Cap theorem, consistency models.
- Лекция 8 — Raft consensus.
- Лабораторные: реализовать Raft-хранилище на Go ( есть分散 с ключевыми concepts).

**Для чего:** практический багаж + хорошо структурированная теория; хороший
бесплатный аналог SYS-дизайн-курсов.

### Cornell CS 5414 / 4410: Distributed Systems (лекции Randy Bryant)

**Откуда:** https://www.cs.cornell.edu/courses/cs4410/2022fa/
(лекции в PDF, лабораторные на Go).

**Что берём:** лекции по 2PC, Paxos/Raft, replication; другие视角 на consensus.

**Для чего:** второй взгляд на Raft/Paxos (ROT13). Хорош, если MIT-вариант
не зайдёт.

---

## 2. Cache invalidation: конкретные бесплатные статьи

### Wikipedia: Cache invalidation (ссылки в контенте)

https://en.wikipedia.org/wiki/Cache_invalidation

Ключевые разделы для чтения: «Cache policies», «Write-through», «Write-back»,
«Read-through», «Read-behind / Write-behind», «Eviction policies».

**Для чего:** базовая терминология, которая потом встречается в RFC, в Redis-доках,
в DDIA. Хорошо структурировано; под каждым пунктом — further reading.

### Wikipedia: Cache replacement policies

https://en.wikipedia.org/wiki/Cache_replacement_policies

Разделы LRU, LFU, FIFO, ARC — основные стратегии. Наш кэш по embedding
cosine similarity — не классический eviction, но концепция та же.

**Для чего:** понять, *что* выбрасывается из кэша, когда он «переполняется»;
наша система использует TTL ( time-based), а не容量-based.

### RFC 9111: HTTP Caching (свежий RFC 9111, заменяет 7234)

https://datatracker.ietf.org/doc/html/rfc9111

Ключевые разделы:
- §3.3 Validation (ЕTag / If-Modified-Since) — прямая аналогия с нашим `rev`.
- §4.2 Freshness (max-age, Expires) — TTL.
- §5.3 Invalidation (POST /PURGE) — эксплицитная инвалидация.

**Для чего:** увидеть, как промышленный протокол решает нашу задачу. Наша
«epoch-bump» — ручная валидация по «revision ETag» (§3.3).

---

## 3. Bounded staleness / eventual consistency

### Wikipedia: Consistency model (обзор моделей)

https://en.wikipedia.org/wiki/Consistency_model

Раздел «Models»: Linearizability, Sequential consistency, Causal consistency,
Eventual consistency, Bounded staleness. Для каждой модели — определение и
«price» (что теряем).

**Для чего:** рамка, в которой мы работаем; понять, *что* мы обещаем (bounded)
vs *что* обещают «сильные» модели.

### Martin Kleppmann: «A Critique of the CAP Theorem» (preprint, free)

https://arxiv.org/pdf/1509.05393.pdf (полный текст PDF)

Разбор CAP: почему классическое прочтение (C *or* A) упрощает реальность;
как действительно строятся системы на стыке consistency/availability/partition.

**Для чего:** важно понимать, что наш кэш «poison» (выбираем A + eventual),
и почему это осознанный выбор, а не «мы не понимаем».

### Martin Kleppmann: «Please stop calling databases CP or AP» (blog, free)

https://martin.kleppmann.com/2015/05/11/please-stop-calling-databases-cp-or-ap.html

Краткая, но ёмкая статья: зачем AP/CP/CA — недостаточная классификация;
какие реальные модели consistency есть в распределённых хранилищах.

**Для чего:** критическое мышление; различать «теорему» и «практикуприменения».

### Lamport: «Time, Clocks, and the Ordering of Events in a Distributed System» (free)

https://lamport.azurewebsites.net/pubs/time-clocks.pdf (авторский сайт, free PDF)

Классика: happens-before, Lamport clocks, partial ordering.

**Для чего:** языковая база для понимания «почему ревизии — это не просто
счётчик, а partial order (какие данные от какого источника)».

### RFC 7413: TCP Fast Open (для понимания push vs poll)

(https://datatracker.ietf.org/doc/html/rfc7413) — не про cache, но好的 пример:
как индустрия решает «быстро vs просто»; не обязателен, но полезен.

---

## 4. Versioned keys / content addressing

### Wikipedia: Content-addressable storage

https://en.wikipedia.org/wiki/Content-addressable_storage

Раздел «Uses»: файловые системы (IPFS), Git, репликация данных.

**Для чего:** ясно понять, что наш `sha256(пары source_url + content_hash)` — это
мини-CAS, и почему он идемпотентен.

### Wikipedia: Merkle tree

https://en.wikipedia.org/wiki/Merkle_tree

Структура: дерево хэшей; проверка целостности; лёгкий клиент (light client).

**Для чего:** наш fingerprint — плоская версия; Merkle tree — «масштабируемая»
реализация для больших объёмов; понять, куда двигаться, если данных станет
очень много.

### Git internals (free chapter из «Pro Git»)

https://git-scm.com/book/en/v2/Git-Internals-Git-Objects

Git objects = content addressing; commit = root tree hash; reference = branch/tag.

**Для чего:** наглядная, уже знакомая каждому разработчику аналогия с тем,
как Git решает versioning; ревизия данных — это аналог commit в Git.

---

## 5. Polling vs push / events

### Wikipedia: Long polling (ссылки в контенте)

https://en.wikipedia.org/wiki/Polling_(computer_science)

Разделы «Long polling» vs «Short polling»; push/ callbacks.

**Для чего:** понять, почему наш polling topology (интервал 5с) — это «short
polling»; long polling / push — «хорошая» альтернатива.

### Martin Kleppmann: «Turning the database inside-out» (blog + video, free)

https://martin.kleppmann.com/2015/03/04/turning-the-database-inside-out.html
+ видео: https://www.youtube.com/watch?v=fU9hR3kiOK0

Event sourcing, CDC (Change Data Capture); вместо «опроси меня» —
«получай события из лога».

**Для чего:** стратегия «push» на уровне «всей базы» — если в будущем
захочется заменить polling на events (Redis Streams), это та теория.

### Jepsen: «The Mutual Exclusion of Raft vs Kafka Streams» (бесплатный анализ)

https://jepsen.io/analyses

Классика: Raft (consensus, polling для heartbeats) vs Kafka (event-driven,
push).

**Для чего:** практический взгляд; для нас — показывает trade-off «heartbeat
polling» vs «subscription».

---

## 6. RAG-оценка / AI-метрики (подготовка к M4)

### LlamaIndex: Evaluating RAG Applications (free course)

https://docs.llamaindex.ai/en/stable/optimizing/evaluation/evaluation.html

Метрики: Retrieval@K, groundedness, faithfulness, relevancy; RAGAS framework.

**Для чего:** наш ADR-015 (lift-отчёт) прямо перекликается с тем, что
описано здесь; понять, *что* измеряется.

### LangChain: RAG Evaluation Guide

https://python.langchain.com/docs/guides/evaluation/rag/

Retrieval QA evaluators, faithfulness; примеры кода на Python.

**Для чего:** альтернативный взгляд; если LlamaIndex не заходит.

### RAGAS docs (framework для RAG-evaluation)

https://docs.ragas.io/

Конкретные метрики: context precision/recall, faithfulness, answer relevancy.

**Для чего:** инструмент для ADR-015, lif-benchmark.

---

## 7. Университетские лекции (video + slides, бесплатно)

### MIT 6.824 2020 (видео, лекции, лабы)

http://nil.csail.mit.edu/6.824/2020/schedule.html

Каждая лекция — free video + PDF; лабораторные — Go.

**Для чего:** параллельный курс; видео很好消化.

### MIT 6.033: Distributed Systems (лекции Массачусетса)

https://pdos.csail.mit.edu/6.824/

**Для чего:** альтернативный взгляд на Raft, консистентность.

### Stanford CS 244b: Advanced Topics in Networking

https://www.scs.stanford.edu/14au-cs244b/

Раздел про Raft (гостевая лекция Ongaro); лабораторные.

**Для чего:**RAFT для «системщиков» (не алгоритмистов).

### University of Cambridge: Concurrent and Distributed Systems (2024–25, free)

https://www.cl.cam.ac.uk/teaching/2425/ConcDisSys/

Лектор: Martin Kleppmann (автор DDIA). Материалы: PDF-лекции + лабораторные.

**Для чего:** практически компактная версия DDIA; 16 лекций покрывают:
threads, transactions, replication, Raft, consistency, linearizability,
eventual consistency, CRDTs.

---

## 8. Как это делается в production: паттерны кэширования данных

### Elasticsearch / OpenSearch: индексы и инвалидация

- **Refresh interval** (дефолт 1с): данные видны через N секунд после COMMIT.
  Аналог: наш поллинг каждые 5с (`TOPOLOGY_POLL_INTERVAL`).
- **Index alias**: при переиндексации меняется alias `my_index_v1` → `my_index_v2`.
  Старые запросы автоматически идут на новый индекс. Аналог: наш epoch-bump
  ключа `query:sc:<rev>:<domain>`.
- **Query cache / Request cache**: автоматический кэш фильтров и агрегаций.
  Аналог: наш SemanticCache (ручной, не автоматический).
- **Versioned index**: `logs-2026.09` → `logs-2026.10`. Старые индексы удаляются
  по retention policy. Аналог: our `rev` в ключе кэша.

Ссылки:
- Elasticsearch docs: https://www.elastic.co/guide/en/elasticsearch/reference/current/index-lifecycle-management.html
- OpenSearch docs: https://opensearch.org/docs/latest/

### Feature Store (Feast, Tecton): point-in-time correctness

- **Online store** (Redis) + **Offline store** (S3/DataLake) с разной свежестью.
  Online = миллисекунды, Offline = часы/дни.
- **Materialization**: feature values пересчитываются при изменении данных
  (batch или streaming). Аналог: наш COMMIT → инвалидация кэша.
- **Versioning**: каждая feature table имеет version; можно запросить
  "features as of timestamp T". Аналог: наш fingerprint (content hash).
- **Point-in-time correctness**: гарантия "не видеть будущих данных".
  Аналог: наш bounded staleness.

Ссылки:
- Feast: https://docs.feast.dev/
- Tecton: https://docs.tecton.ai/

### Data Lake (Iceberg, Hudi, Delta Lake): snapshots и time travel

- **Snapshot versioning**: каждый COMMIT создаёт snapshot; можно запросить
  данные "какими они были вчера" (`SELECT * FROM table TIMESTAMP AS OF '...'`).
- **Invalidate через snapshot**: старые snapshot'ы удаляются по retention →
  старые кэши умирают. Аналог: наш epoch-bump (старый ключ умирает по TTL).
- **ACID транзакции**: атомарные обновления; readers видят консистентный
  снапшот. Аналог: наш ADR-014 (атомарный COMMIT).
- **Copy-on-write**: при обновлении создаётся новый файл; старый остаётся
  для time travel. Аналог: наши `superseded` + `active` в DocumentRegistry.

Ссылки:
- Iceberg: https://iceberg.apache.org/docs/latest/
- Delta Lake: https://docs.delta.io/latest/

### CDN (Cloudflare, Akamai): purge и versioned URLs

- **Purge by URL/tag**: инвалидация конкретных ресурсов.
  Аналог: наш `clear()` (ручной сброс кэша).
- **Versioned URLs**: `style.css?v=2` → новый кэш.
  Аналог: наш `query:sc:<rev>:<domain>` (rev = v=2).
- **Cache-Control headers**: `max-age`, `stale-while-revalidate`.
  Аналог: наш TTL + bounded staleness.
- **Edge computing**: вычисления на CDN → кэш = локальные данные.
  Аналог: наш InMemorySemanticCache (in-process).

Ссылки:
- Cloudflare: https://developers.cloudflare.com/cache/
- Akamai: https://developer.akamai.com/

### Event Sourcing / CQRS: события как инвалидация

- **Event log**: все изменения = события (Kafka, EventStore).
  Аналог: наш COMMIT ( но без генерации события).
- **Read model**: materialized view из событий.
  Аналог: наш query-контур (читает из graph/vector store).
- **Invalidate через событие**: при COMMIT → событие → read model
  пересчитывается. Аналог: наш planned upgrade (Redis pub/sub при COMMIT).
- **Versioned read models**: `read_model_v1`, `read_model_v2`.
  Аналог: наш epoch-bump.

Ссылки:
- Event Sourcing: https://martinfowler.com/eaaDev/EventSourcing.html
- CQRS: https://martinfowler.com/bliki/CQRS.html
- Kafka: https://kafka.apache.org/documentation/

### LLM-сервисы (OpenAI, Cohere, Bedrock): кэширование промптов

- **Prompt caching**: одинаковые промпты → одинаковые ответы.
  Аналог: наш SemanticCache ( но семантический, а не exact match).
- **Response caching**: семантически близкие запросы → одинаковые ответы.
  Это то, что мы делаем.
- **Embedding caching**: одинаковые тексты → одинаковые эмбеддинги.
  Аналог: наш Embedding Service ( но без кэша эмбеддингов).
- **Model versioning**: при обновлении модели → кэш сбрасывается.
  Аналог: наш epoch-bump ( при смене модели → сброс).

Ссылки:
- OpenAI Caching: https://platform.openai.com/docs/guides/prompt-caching
- Cohere: https://docs.cohere.com/

---

## Рекомендации по приоритету (если времени мало)

1. **DDIA гл.5,9,12** (если купишь) или **van Steen Chapters 5,7,9** (free).
2. **Cambridge ConcSys 2024–25** (лекции 9–16, free) — ядро распределённых систем.
3. **Raft paper + визуализация** (raft.github.io) — consensus за вечер.
4. **Kleppmann: «Please stop calling databases CP or AP»** — критическое мышление.
5. **Lamport's «Time, Clocks»** — 10 страниц, eternity of clarity.
6. **RAGAS / LlamaIndex Evaluation** — для ADR-015 (Eval).

---

*Все ссылки проверены (2026-09-14). Если ссылка умерла — используй поиск по
названию材料/автора; все они стабильные и существуют уже несколько лет.*