# GraphRAG: Мультиязычная платформа Enterprise GraphRAG (v5.0)

> **Версия:** 5.0  
> **Дата:** 04.09.2026  
> **Статус:** Финальная спецификация

---

## 1. Обзор системы и архитектурные принципы

GraphRAG v5 — доменно-агностичная платформа для извлечения, хранения и семантического поиска сущностей из неструктурированных текстов на русском и английском языках.

---

## 2. Слой адаптеров (новое в v5)

Ядро системы взаимодействует с инфраструктурой исключительно через программные интерфейсы (GraphStoreProvider, VectorStoreProvider, LLMInference, Embedder, Reranker). Конкретная реализация выбирается через runtime config (namespace: adapters) и загружается динамически при старте.

### 2.1. Graph & Vector Storage Adapters (разделение интерфейсов)

> Монолитный интерфейс `GraphStorage` аннулирован (перенесён в ADR-013). В гибриде граф и вектор — две независимые оси поиска, поэтому они представлены двумя изолированными ABC-контрактами, которые соединяются только на этапе Context Assembly.

```
interface GraphStoreProvider:
    - query(cypher, params) -> results
    - upsert_nodes(nodes)
    - upsert_edges(edges)
    - get_node(id) -> node
    - delete_node(id)
```

```
interface VectorStoreProvider:
    - vector_search(embedding, top_k) -> chunks
    - upsert_vectors(chunk_id, embedding)
```

**Реализации (Фаза 1 — прототип):**
- `Neo4jGraphStore` — Bolt-драйвер, Cypher-запросы, обход связей, накат constraints (базовая).
- `Neo4jVectorStore` — нативный векторный индекс Neo4j (базовая).

**Реализации (Фаза 2 — масштабирование):**
- `MemgraphGraphStore` — Cypher-совместимая альтернатива для графовой оси.
- `QdrantVectorStore` — кластерный векторный поиск, подменяет Neo4jVectorStore без изменений графовой логики.

### 2.2. LLM Adapter

```
interface LLMInference:
    - generate(system_prompt, user_prompt, params) -> response
    - generate_stream(system_prompt, user_prompt, params) -> token_stream
```

**Реализации:**
- `OllamaAdapter` — HTTP к :11434, Qwen 2.5 7B (базовая).
- `OpenAICompatibleAdapter` — vLLM, LM Studio, любой /v1/chat/completions.
- `VllmAdapter` — оптимизированный клиент vLLM с батчингом.

### 2.3. Embeddings Adapter

```
interface Embedder:
    - embed_batch(texts) -> vectors
    - embed_single(text) -> vector
```

**Реализации:**
- `BgeM3ServiceAdapter` — HTTP к Embeddings Service :8004 (базовая).
- `LocalSentenceTransformerAdapter` — встроен в пайплайн, без отдельного контейнера.
- `OpenAIEmbeddingsAdapter` — OpenAI Embeddings API.

### 2.4. Reranker Adapter

```
interface Reranker:
    - rerank(query, chunks) -> ranked_chunks
```

**Реализации:**
- `BgeRerankerAdapter` — bge-reranker-base на CPU (базовая).
- `NoOpRerankerAdapter` — заглушка, возвращает входной массив без изменений.
- `CohereRerankAdapter` — Cohere Rerank API (облако).

### 2.5. Runtime Configuration

Выбор адаптера — через `namespace: adapters` в Config Service:

```yaml
# infra/config/adapters.yaml
adapters:
  graph_store: "neo4j_graph"  # neo4j_graph | neo4j_grpc_graph | memgraph_graph
  vector_store: "neo4j_vector" # neo4j_vector | qdrant
  llm: "ollama"              # ollama | openai_compatible | vllm
  embeddings: "bge_m3_service" # bge_m3_service | local_sentence_transformer | openai
  reranker: "bge_reranker"   # bge_reranker | noop | cohere
```

Переключение без перезапуска контейнеров:

```bash
curl -X PUT http://localhost:8001/api/v1/config/adapters \
  -d '{"vector_store": "qdrant"}'
```

### 2.6. Регистрация сторонних адаптеров

Сторонняя реализация адаптера регистрируется через `entry_points` (Python setuptools):

```toml
# pyproject.toml стороннего пакета
[project.entry-points."graphrag.adapters.storage"]
my_custom_db = "my_package:MyCustomDBAdapter"
```

Ядро автоматически загружает адаптер по имени из `entry_points` при старте. Регистрация не требует изменения исходного кода ядра.

---

## 3. Архитектура доменов

### 3.1. Принцип разделения

```
+----------------------------------------------------------+
|                    ЯДРО СИСТЕМЫ (fixed)                   |
|                                                           |
|  Ingestion Pipeline (9 этапов)                            |
|  Retriever (Graph + Vector + Reranker + Context)         |
|  Services (Query, Config, Glossary, Ingestion)           |
|  Adapter Layer (GraphStore, VectorStore, LLM, Embedder, Reranker) |
|  Observability (Prometheus + Grafana + Loki)             |
+----------------------------------------------------------+
                                 |
                                 | настраивается через
                                 v
+----------------------------------------------------------+
|                DOMAIN PROFILE (configurable)              |
|                                                           |
|  Ontology Schema (типы узлов, типы рёбер)                 |
|  Extraction Prompt Template (доменный промпт)              |
|  Validation Rules (правила семантической валидации)      |
|  Glossary Content (синонимы, канонизация, Unicode)       |
|  Chunking Strategy (стратегия разрезания по типам источника)|
|  Context Assembly Template (формат сборки промпта)        |
+----------------------------------------------------------+
```

Ядро не знает, с каким доменом работает. Domain Profile определяет:
- Какие типы узлов и рёбер существуют
- Какие правила валидации применять
- Какой промпт использовать для извлечения
- Как канонизировать имена сущностей
- Как разрезать документы на чанки
- Как собирать контекст для LLM

---

## 4. Ingestion Pipeline

### 4.1. Динамический Ingestion Pipeline (9 этапов)

Движок последовательно прогоняет данные через этапы:

1. **INGEST** — Приём документа через Ingestion API (:8002). Поддержка: .txt, .md, .pdf, .json. Метаданные: source_url, domain, doc_type.
2. **CHUNK** — Фрагментация текста. Стратегия: sliding window с overlap (chunk_size=512, overlap=64 токена). Сохранение: Chunk-узлы с CONTAINS-связями к Source.
3. **EMBED** — Генерация векторных embeddings через **Embeddings Adapter**. Выбор модели — через runtime config (namespace: adapters.embeddings). Базовая реализация: bge-m3 (1024 dim), HTTP к Embeddings Service :8004. Альтернатива: LocalSentenceTransformerAdapter — встроен в пайплайн, без отдельного контейнера. Batch: 32 фрагмента за запрос (настраивается: namespace: embeddings.batch_size). Ядро не знает, какой эмбеддер под капотом.
4. **EXTRACT** — Сырая экстракция сущностей через **LLM Adapter**. Выбор модели — через runtime config (namespace: adapters.llm). Базовая реализация: OllamaAdapter — HTTP к :11434, Qwen 2.5 7B. Альтернатива: OpenAICompatibleAdapter — vLLM, LM Studio, любой /v1/chat/completions. Промпт: доменный prompt_template из активного Domain Profile. Модель отвечает ТОЛЬКО за экстракцию сырых сущностей и связей. Гарантия детерминированности — на стороне Python (нормализация).
5. **NORMALIZE** — Контекстно-зависимая канонизация. Правила канонизации берутся из Domain Profile (canonicalization). Математические символы (Big-O) изолированы от текстовых полей. Unicode-нормализация через таблицу unicode_map из Glossary Service. LLM-fallback: при сбое regex-валидации — автоматический fallback на исходную строку + warning в лог.
6. **DEDUP** — Двухступенчатая дедупликация. Ступень 1 (auto): косинус >= 0.92 → автоматическое слияние. Эмбеддинги получаются через **Embeddings Adapter**. Ступень 2 (LLM): зона 0.75–0.92 → верификация через **LLM Adapter**. LLM-fallback: при недоступности LLM — сохранение как separate entities + связь SIMILAR_TO + warning. Зона < 0.75 → разные сущности, не склеиваются.
7. **CONTRACT** — Склейка вложенных JSON-схем. Поиск связей EXTENDS и REFERENCES ($ref, allOf) для построения иерархии Contract-узлов.
8. **VALIDATE** — Семантическая валидация графа. Cypher-правила из Domain Profile (validation_rules). Типы ошибок: structural (нет обязательного поля), semantic (логические противоречия).
9. **COMMIT** — Атомарная запись в хранилище через **GraphStoreProvider** и **VectorStoreProvider**. Используется транзакция с rollback при ошибке. После коммита — обновление Document Registry.

---

## 5. Стратегия ретривера и слияния контекста

При запросе пользователя Query API последовательно выполняет следующие шаги:

1. **Расчёт эмбеддинга запроса** — через **Embeddings Adapter** (модель bge-m3 на GPU, ~50 мс).
2. **Graph Retriever** — выполнение параметризованного Cypher-шаблона через **GraphStoreProvider**. Шаблон автоматически подставляет типы узлов и правила расширения связей (SIMILAR_TO) из метаданных активного Domain Profile.
3. **Vector Retriever** — поиск топ-N релевантных текстовых чанков через **VectorStoreProvider** (vector_search). Обращается параллельно с Graph Retriever как независимая ось.
4. **Reranker** — переранжирование чанков через **Reranker Adapter**. Базовая реализация: bge-reranker-base на CPU. Альтернатива: NoOpRerankerAdapter (отключён, возвращает входной массив без изменений).
5. **Context Assembly** — сборка итогового промпта (см. правила ниже).
6. **LLM Generation** — передача промпта и системных инструкций через **LLM Adapter** (Qwen 2.5 7B Instruct на GPU). Время генерации: от 3 до 10 сек.
7. **Response** — потоковый стриминг токенов ответа пользователю через SSE (Server-Sent Events) с выдачей списка источников (sources) и таймингов.

### Динамическая сборка контекста (Context Assembly)

Формат сборки контекста определяется флагами из Domain Profile:

- **Порядок данных:** Результаты графового поиска ("Скелет") всегда вставляются первыми, формируя логический каркас для ЛЛМ. Векторные чанки ("Тело") идут вторыми, наполняя каркас формулами, цитатами и кодом.
- **Шаблон контекста:** Подгружается по ID из профиля (context_it_v1, context_cinema_v1).
- **Окно контекста:** Жёсткий лимит в 4096 токенов контролируется программно.
- **Стратегия вытеснения при переполнении лимита:** Если объём контекста превышает 4096 токенов, излишки детерминированно отбрасываются. При этом первоочерёдно отбрасываются векторные чанки с наименьшим reranker-score.

---

## 6. Конфигурация сервисов и Runtime API

### 6.1. Сетевая архитектура и карта портов

Все сервисы изолированы внутри единой виртуальной Docker-сети с именем "ohw_net" (Ollama Homework Network).

| Сервис              | Порт          | Назначение                                         |
|---------------------|---------------|----------------------------------------------------|
| Query API           | 8000          | Поисковые запросы и генерация ответов              |
| Config Service      | 8001          | Хранение Feature Flags + Управление Domain Profile + Adapters API |
| Ingestion API       | 8002          | Управление фоновыми джобами индексации            |
| Glossary Service    | 8003          | Канонизация и загрузка glossary.{profile}.yaml     |
| Embeddings Service  | 8004          | Расчёт векторов bge-m3 на GPU (опционально, если не используется встроенный адаптер) |
| Neo4j Database      | 7687 (Bolt)   | Bolt-интерфейс для работы с графом                 |
| Neo4j Database      | 7474 (HTTP)   | Web UI браузера для Neo4j                          |
| Ollama Server       | 11434         | Инференс локальной модели Qwen 2.5 7B             |

### 6.2. Runtime Domain Management API (Config Service)

Управление доменными конфигурациями осуществляется через REST API без перезапуска контейнеров:

- `GET /api/v1/config/domain/active` — Получить имя активного профиля
- `GET /api/v1/config/domain/profiles` — Получить список доступных YAML-профилей
- `GET /api/v1/config/domain/profile/{name}` — Получить содержимое профиля {name}
- `POST /api/v1/config/domain/validate` — Валидация структуры нового YAML-профиля
- `POST /api/v1/config/domain/profile` — Загрузка нового профиля в систему
- `POST /api/v1/config/domain/activate` — Рантайм-переключение активного домена

### 6.3. Adapters Management API (новое в v5)

Управление слоем адаптеров осуществляется через REST API без перезапуска контейнеров:

- `GET /api/v1/config/adapters` — Получить текущие адаптеры
- `PUT /api/v1/config/adapters` — Изменить адаптеры (переключение на лету)
- `GET /api/v1/config/adapters/available` — Получить список доступных адаптеров (включая плагины)

Примеры:

```bash
# Получение текущих адаптеров
curl http://localhost:8001/api/v1/config/adapters

# Смена векторной оси хранилища на Qdrant
curl -X PUT http://localhost:8001/api/v1/config/adapters \
  -d '{"vector_store": "qdrant"}'

# Получение списка доступных адаптеров (включая плагины)
curl http://localhost:8001/api/v1/config/adapters/available
```

### 6.4. Структура изолированных глоссарей

**Роль Glossary Service:** доменный сервис трансляции «тегов» (синонимы и варианты записи) в канонический ряд для графа (`canonical_name`). Лингвист/аналитик работает через GUI-модель тегов (экран «Бизнес-онтология» конфигуратора); технический формат узлов внутри сервиса скрыт. При сохранении словаря выполняется валидация уникальности: в рамках домена один вариант записи не может вести к двум каноническим терминам; пересечения словарей с активными профилями проверяются через `POST /api/v1/config/domain/validate`.

Glossary Service подгружает соответствующий файл глоссария вслед за активацией домена:

- `glossary.library.yaml` — содержит terms (авторы), genre_aliases, pen_names, unicode_map
- `glossary.cinema.yaml` — содержит terms (фильмы), director_aliases, genre_aliases
- `glossary.it.yaml` — содержит terms, data_types, complexity_aliases, unicode_map, а также секцию function_synonyms

### 6.5. Конфигурация сервисов

#### Полный набор ключей runtime config (Config Service namespaces)

**namespace: domain**
- `active_profile` ("it")
- `profiles_available`
- `auto_reload`

**namespace: retrieval**
- `cosine_threshold` (0.7)
- `max_graph_nodes` (5)
- `max_vector_chunks` (5)
- `context_size` (4096)
- `reranker_enabled`
- `graph_search_enabled`
- `similar_to_expansion`

**namespace: extraction**
- `model` ("qwen2.5:7b-instruct")
- `temperature` (0.1)
- `max_tokens` (4096)
- Примечание: доменный prompt_template подгружается динамически из профиля

**namespace: normalizer**
- `dedup_auto_threshold` (0.92)
- `dedup_llm_threshold` (0.75)
- `similar_to_threshold` (0.85)
- `llm_canonicalize_fallback` (true)
- `unicode_normalization` (true)
- `log_normalization` (true)

**namespace: llm**
- `model` ("qwen2.5:7b-instruct")
- `temperature` (0.3)
- `max_tokens` (2048)
- `context_window` (32768)

**namespace: embeddings**
- `model` ("bge-m3")
- `dimensions` (1024)
- `max_tokens` (8192)
- `batch_size` (32)

**namespace: storage**
- `graph_store` ("neo4j_graph") — графовая ось: Neo4jGraphStore / MemgraphGraphStore.
- `vector_store` ("neo4j_vector") — векторная ось: Neo4jVectorStore / QdrantVectorStore.
- `neo4j_uri` ("bolt://neo4j:7687")

**namespace: auth**
- `api_key` ("changeme")

**namespace: flags**
- `graph_search_enabled`
- `reranker_enabled`
- `similar_to_expansion`
- `semantic_validation`

**namespace: adapters (новое в v5)**
- `graph_store` ("neo4j_graph")
- `vector_store` ("neo4j_vector")
- `llm` ("ollama")
- `embeddings` ("bge_m3_service")
- `reranker` ("bge_reranker")

---

## 7. Журнал архитектурных решений (ADR-001 — ADR-013)

### ADR-001: Выбор Neo4j в качестве единого хранилища для прототипа

**Статус:** Accepted  
**Контекст:** Нужно выбрать хранилище для графа знаний и векторных чанков.  
**Решение:** Neo4j Community (GPLv3, native vector index, Cypher).  

**Последствия:**
- + Один источник правды, нет JOIN между БД
- + Cypher для графовых запросов
- - GPLv3 лицензия (приемлемо для internal use)
- - Vector index деградирует на >1M чанков → Qdrant (фаза 2)

---

### ADR-002: Выбор Qwen 2.5 7B Instruct как LLM

**Статус:** Accepted  
**Контекст:** Нужна локальная LLM для генерации + extraction, без API.  
**Решение:** Qwen 2.5 7B Instruct через Ollama.  

**Последствия:**
- + Работает на 1 GPU (8 GB VRAM достаточно)
- + Хороший русский язык, instruction-tuned
- - 7B может галлюцинировать → Validator + ручной ревью

---

### ADR-003: Выбор bge-m3 для эмбеддингов

**Статус:** Accepted  
**Контекст:** Нужны мультиязычные (RU/EN) эмбеддинги, 1024 dim.  
**Решение:** bge-m3 (BAAI, Apache 2.0).  

**Последствия:**
- + 1024 dim, мультиязычный, хорошее качество на русском
- - 2.3 GB весов, ~4 GB VRAM
- - Не может работать одновременно с Qwen 7B на одном GPU

---

### ADR-004: Разделение LLM и Python в пайплайне

**Статус:** Accepted  
**Контекст:** LLM 7B хорошо извлекает сырые триплеты, но плохо выполняет дедупликацию и нормализацию.  
**Решение:** LLM — только экстракция. Python — дедупликация, канонизация, семантический вывод.  

**Последствия:**
- + Стабильное качество (детерминированный Python)
- + Меньше вызовов LLM
- - Normalizer сложнее (4 подэтапа)

---

### ADR-005: Двухступенчатая векторная дедупликация

**Статус:** Accepted  
**Контекст:** Один порог 0.92 отсекает явные дубли, но пропускает пограничные случаи.  
**Решение:**
- Ступень 1: cosine >= 0.92 → авто-merge
- Ступень 2: 0.75 <= cosine < 0.92 → LLM (SAME/DIFFERENT)
- Ступень 3: cosine < 0.75 → пропустить

**Последствия:**
- + Выше точность
- - Доп. нагрузка на GPU для ступени 2

---

### ADR-006: Иерархия Contract-узлов

**Статус:** Accepted  
**Контекст:** JSON-схемы ссылаются друг на друга через $ref и allOf.  
**Решение:** Добавить связи EXTENDS и REFERENCES между :Contract.

**Последствия:**
- + Граф знает структуру вложенных схем
- + Graph Retriever обходит дерево одним Cypher
- - Extraction-промпт сложнее

---

### ADR-007: Контекстно-зависимая канонизация строковых представлений

**Статус:** Accepted  
**Контекст:** LLM создаёт дубли из-за пробелов, регистра, форматирования (O(V^2) vs O(v^2) vs O(V ** 2)).  
**Решение:** Контекстно-зависимая канонизация:
- complexity: 4 слоя (Unicode → функции → структура → glossary)
- algorithm: 2 слоя (Unicode → glossary)
- data_type: 1 слой (glossary only)
- прочие: trim + нижний регистр

**Последствия:**
- + Меньше дублей, чище поиск
- + Правила расширяются через YAML, без деплоя
- - Канонизация может быть агрессивной (edge cases)
- - Нужна поддержка таблиц в Glossary Service

---

### ADR-008: Семантическая валидация графа

**Статус:** Accepted  
**Контекст:** Структурной валидации недостаточно. Система должна блокировать логические противоречия на этапе ingestion.  
**Решение:** Validator v2 проверяет:
- REQUIRES_CONSTRAINT без цели → warning
- CONTRADICTS внутри одного требования → error
- REQUIRES + CONTRADICTS одновременно → error
- SIMILAR_TO без обратной связи → auto-fix

**Последствия:**
- + Ошибки ТЗ ловятся до продакшена
- - Может давать false positives → нужен ревью отчётов

---

### ADR-009: LLM-fallback для канонизации сложности

**Статус:** Accepted  
**Контекст:** Слои 1-3 (Unicode, синонимы функций, структурное сжатие Big-O) работают детерминированно, но не покрывают все случаи человеческих записей формул.  
**Решение:** Добавить LLM-fallback через **LLM Adapter** с обязательной проверкой результата регулярным выражением (Regex) по строгому паттерну o(...). При сбое Regex-валидации — автоматический fallback на исходную строку + warning в лог.

---

### ADR-010: Unicode-нормализация математических символов

**Статус:** Accepted  
**Контекст:** Математические символы (√, ², Σ, α) часто используются в текстах, но затрудняют сравнение и поиск.  
**Решение:** Вынесение таблицы unicode_map в управляемый Glossary Service без изменения логики исходного кода.

**Последствия:**
- + Трансформация символов контролируется через YAML
- + Можно добавлять новые символы без деплоя
- - Требует согласованной работы с другими слоями канонизации

---

### ADR-011: Переход к доменно-агностичной архитектуре платформы (ВЕРСИЯ 4.0)

**Статус:** Accepted  
**Контекст:** Жесткая привязка кода к ИТ-домену ограничивала масштабируемость.  
**Решение:** Реализовать концепцию Domain Profile (YAML). Вся онтология, промпты, правила валидации на Cypher и канонизация вынесены в конфиги. Ядро системы абсолютно агностично. Смена домена происходит на лету через Config Service API.

**Последствия:**
- + Один движок — множество доменов
- + Новый домен = новый YAML, без кода
- + Переключение домена в runtime (Config Service API)
- + Сообщество может создавать свои профили
- - Качество extraction зависит от промпта (нужно калибровать)
- - Мультидоменность в одной инсталляции — перспектива (namespace)
- - Валидация Domain Profile при загрузке (синтаксис, ссылки)

---

### ADR-012: Введение слоя адаптеров для изоляции инфраструктуры (ВЕРСИЯ 5.0)

**Статус:** Accepted  
**Контекст:** В v4 ядро системы было жёстко привязано к конкретным технологиям (Neo4j, Ollama, bge-m3 HTTP Service, bge-reranker). Замена любого компонента требовала изменения кода ядра. Это ограничивало гибкость и создавало vendor lock-in.  
**Решение:** Ввести слой адаптеров — программные интерфейсы (GraphStoreProvider, VectorStoreProvider, LLMInference, Embedder, Reranker), изолирующих ядро от конкретных технологий. Конкретная реализация выбирается через runtime config (namespace: adapters) и загружается динамически при старте. Сторонние адаптеры регистрируются через entry_points (Python setuptools).

**Последствия:**
- + Замена инфраструктуры — правка одного конфига, без переписывания ядра
- + Поддержка множества хранилищ (Neo4j, Qdrant, Memgraph), LLM (Ollama, vLLM, OpenAI), эмбеддингов (bge-m3, sentence-transformers), реранкеров (bge-reranker, NoOp, Cohere)
- + Сторонние разработчики могут создавать свои адаптеры без изменения кода ядра
- + Снижение vendor lock-in риска
- + Возможность выбора оптимального стека под задачу (лёгкая инсталляция, замена хранилища, облачный LLM)
- - Увеличение сложности архитектуры (дополнительный слой абстракции)
- - Необходимость контрактных тестов для каждого адаптера
- - Небольшой оверхед на вызов через интерфейс (незначительный)

---

### ADR-013: Разделение интерфейсов графового и векторного хранилищ (уточнение ADR-012)

**Статус:** Accepted  
**Контекст:** Монолитный интерфейс `GraphStorage`, введённый в ADR-012, концептуально неверно моделировал гибрид. Чисто векторный поиск ложно зависел от логики графового движка, а `QdrantStorageAdapter` (только векторы) не мог корректно реализовать графовые операции. Это нарушало принцип разделения интерфейсов (ISP) и блокировало независимое масштабирование осей.  
**Решение:** Аннулировать `GraphStorage`. Ввести два изолированных ABC-контракта: `GraphStoreProvider` (логика связей, Cypher, обход) и `VectorStoreProvider` (косинусный поиск чанков). Гибридный ретривер обращается к ним параллельно, соединяя результаты только на этапе Context Assembly. В прототипе обе реализации смотрят на Neo4j (native graph engine + native vector index); при росте `VectorStoreProvider` бесшовно заменяется на `QdrantVectorStore` без изменения графовой логики.

**Последствия:**
- + Соответствие ISP: граф и вектор — две независимые оси.
- + Бесшовная миграция векторов на Qdrant подменой одной реализации, Cypher-пайплайны графа не затрагиваются.
- + Qdrant больше не «притворяется» графовым хранилищем; гибридность сохраняется (граф живёт в Neo4j/Memgraph, векторы — в Qdrant).
- - В Query Workers необходимо инициализировать два отдельных клиента хранилища.
- - Кросс-правка всех упоминаний `GraphStorage` в спецификациях (SSOT).

---

## 8. Регламент Operations, масштабирования и рисков

### 8.1. Регламент наблюдаемости (Observability) и метрики PromQL

Стек: structlog (Сбор JSON-логов) -> stdout -> Promtail -> Loki -> Grafana  
      Prometheus -> Сбор метрик по порту /metrics -> Grafana  
      DCGM Exporter -> Мониторинг VRAM и температуры GPU

Ключевые бизнес-метрики в Prometheus (все содержат обязательный лейбл {domain}):

- `graphrag_query_total{status, domain}` — Счетчик поисковых запросов
- `graphrag_query_duration_seconds{stage, domain}` — Тайминги (graph/vector/rerank/llm)
- `graphrag_ingestion_duration_seconds{stage, domain}` — Скорость работы пайплайна загрузки
- `graphrag_graph_nodes_total{domain, type}` — Общее число узлов в разрезе типов
- `graphrag_graph_edges_total{domain, type}` — Общее число рёбер в разрезе типов
- `graphrag_dedup_merges_total{stage="auto|llm"}` — Счетчик авто- и ЛЛМ-мержей
- `graphrag_validation_errors_total{type, rule, domain}` — Ошибки (structural/semantic)
- `graphrag_canonicalization_total{layer, domain}` — Статистика слоёв нормализатора
- `graphrag_canonicalization_fallback_total{domain}` — Счетчик вызовов ЛЛМ-fallback
- `graphrag_domain_switch_total` — Метрика частоты смены доменов
- `graphrag_adapter_switch_total{adapter_type}` — Метрика смены адаптеров (новое в v5)

### 8.2. Профили Docker Compose (Управление ресурсами железа)

Запуск компонентов изолирован через Docker Profiles для экономии ОЗУ и VRAM:

- **"config"** — Контейнеры Config Service + Glossary Service. Работают ВСЕГДА.
- **"graph"** — Контейнер Neo4j Community (или другой через адаптер). Работает ВСЕГДА.
- **"embeddings"** — Embeddings Service (bge-m3). Включается только на фазе индексации (если не используется встроенный адаптер).
- **"ingestion"** — Ingestion API + Скрипты пайплайна. Поочерёдно забирает GPU.
- **"llm"** — Query API + Ollama (Qwen 7B). Включается на фазе поиска.
- **"reranker"** — bge-reranker-base (CPU). Опциональный контейнер фазы поиска (если не используется NoOp-адаптер).
- **"monitoring"** — Prometheus + Grafana + Loki. Разворачивается по требованию.

### 8.3. Стратегия масштабирования (Рост системы в 1000 раз)

| Параметр             | Прототип          | Рост              | Продакшен           |
|----------------------|-------------------|-------------------|---------------------|
| Оркестрация          | Docker Compose    | K3s (Apache 2.0)  | Managed K8s         |
| Векторное хранилище  | Neo4j (native)    | Qdrant (отдельно) | Qdrant (кластер)    |
| Конфиг-хранилище     | SQLite            | Postgres          | etcd (Apache 2.0)   |
| Глоссарий            | YAML + SQLite     | Postgres          | Postgres            |
| Кэш                  | Нет               | Valkey (Redis)    | Valkey (кластер)    |
| Очередь ingestion    | Синхронно         | Redis / RabbitMQ  | Kafka               |
| Модель LLM           | Qwen 7B (1 GPU)   | Qwen 14B (1 GPU)  | Qwen 72B (multi-GPU)|
| Модель эмбеддингов   | bge-m3 (1 GPU)    | bge-m3 (1 GPU)    | bge-m3 (отдельный)  |
| GPU                  | 1 (поэтапно)      | 2 (параллельно)   | N (автоскейлинг)    |

### 8.4. Матрица критических рисков

Примечание: Ниже зафиксированы 6 критических инфраструктурных рисков текущей фазы.

| № | Риск                                               | Митигация                                            |
|---|----------------------------------------------------|------------------------------------------------------|
| 1 | GPU-конфликт памяти между bge-m3 и Qwen 7B на одной карте RTX 2070 Super | Жёсткое поочерёдное использование видеокарты через Docker Compose Profiles |
| 2 | OOM (Out of Memory) СУБД Neo4j на хост-машине с 16 ГБ ОЗУ | Ограничение JVM (max heap 1G, pagecache 512M) через параметры контейнера |
| 3 | Ложные склейки сущностей нормализатором на Ступени 1 (порог 0.92) | Двухступенчатая проверка: зона 0.75-0.92 отправляется на верификацию в ЛЛМ |
| 4 | Раздувание и замусоривание графа связями SIMILAR_TO | Повышенный порог косинуса (0.85) + обязательный вывод отчётов для ручного ревью |
| 5 | Логические противоречия и галлюцинации ЛЛМ на этапе Extraction | Работа Qwen 7B на низкой температуре (0.1) + тотальная семантическая проверка графа Cypher-запросами в Validator v2 до сохранения изменений |
| 6 | Vendor lock-in — привязка к конкретному поставщику инфраструктуры (новое в v5) | Слой адаптеров (ADR-012) позволяет заменить любой компонент инфраструктуры через runtime config без переписывания ядра |
---
