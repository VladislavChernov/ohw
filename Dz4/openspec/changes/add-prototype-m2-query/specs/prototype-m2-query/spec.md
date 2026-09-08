# Delta Spec: add-prototype-m2-query (Query API + Retriever + реальный COMMIT)

## ADDED Requirements

### Requirement: Асинхронный Query API (ADR-016/020, L3-05)

Query API (`:8000`) ДОЛЖЕН принимать запрос асинхронно: `POST /query` → `202 Accepted`
с `{task_id, status, accepted_at}`, отдавать статус через `GET /query/tasks/{task_id}`
(lifecycle `queued → running → succeeded | failed | cancelled`) и доставлять результат
потоком SSE единым конвертом событий ADR-016.

#### Scenario: Приём задачи и опрос статуса

- **WHEN** вызван `POST /query` с валидным телом
- **THEN** ответ `202` с `task_id` и `status: "queued"`, а `GET /query/tasks/{task_id}`
  со временем показывает `running`, затем `succeeded`

#### Scenario: SSE-стрим результата (контект ADR-016)

- **WHEN** клиент подписан на `GET /query/tasks/{task_id}/stream`
- **THEN** он получает последовательность событий `status` → `token*` → `done`
  (в `done`: `text`, `sources[{source_url, relevance}]`, `generation_time_s`)

#### Scenario: Сбой задачи

- **WHEN** при обработке задачи происходит ошибка
- **THEN** задача получает `status: failed`, а стрим отдаёт событие `error`
  с `{code, message}`

### Requirement: Task Queue (Valkey / Redis Streams)

Обработка запросов ДОЛЖНА выполняться пулом Query Workers через Task Queue
(`query:tasks`, XADD/XREADGROUP/XACK) с per-task потоком событий для подписчика SSE.
Тесты ДОЛЖНЫ работать на контрактной in-memory реализации без внешнего Valkey.

#### Scenario: Очередь и воркер (контракт интерфейса)

- **WHEN** задача отправлена в очередь, а воркер её забирает
- **THEN** задача выполняется ровно один раз, события доступны по `task_id`,
  по завершении — `ack`

### Requirement: DeterminRetriever — граф ∥ вектор + Context Assembly (L1-04, L3-03, L3-04, L1-05)

Retriever ДОЛЖЕН выполнять две независимые оси поиска (Graph ∥ Vector) и собирать контекст
детерминированно (Python, без LLM): графовый «скелет» — первым (L3-03), лимит 4096 токенов
жёсткий (L3-04), при переполнении вытесняются только векторные чанки по наименьшему
reranker-score.

#### Scenario: «Скелет» первым и вытеснение по score

- **WHEN** контекст переполняет лимит 4096 токенов
- **THEN** граф-результаты не удаляются, а векторные чанки с наименьшим score
  отбрасываются до укладывания в лимит (порядок «скелет → тело» сохраняется)

### Requirement: Реальный COMMIT в Neo4j (L2-04, L2-03, L2-01, L2-05)

COMMIT ДОЛЖЕН писать узлы/рёбра и векторные эмбеддинги чанков атомарно через
`GraphStoreProvider`/`VectorStoreProvider` (одна транзакция, rollback при ошибке),
MERGE узлов-сущностей по `canonical_name` с `source_ids`/`extractor_version`,
рёбра `CONTAINS`/`SIMILAR_TO`. Soft-delete источника снимает его чанки с поиска.

#### Scenario: Атомарность COMMIT

- **WHEN** на этапе COMMIT происходит ошибка записи
- **THEN** транзакция откатывается, провайдер не содержит частичных записей,
  джоба — `failed`

#### Scenario: Чанки принадлежат источнику (L2-03)

- **WHEN** обработан документ с несколькими чанками
- **THEN** каждый чанк связан с источником ребром `CONTAINS`, орфанные чанки отсутствуют

#### Scenario: Soft-delete снимает чанки с поиска (L2-05)

- **WHEN** источник удалён (soft delete) и повторно выполнен vector/graph поиск
- **THEN** чанки удалённого источника не возвращаются в результатах поиска

### Requirement: Домен-агностичность ретривера (L1-01)

Ретривер ДОЛЖЕН получать Cypher-шаблон и правила расширения связей из метаданных
активного Domain Profile (YAML), а не из кода; смена домена не меняет код ретривера.

#### Scenario: Разные домены — один код

- **WHEN** активирован профиль `library`, затем `it`
- **THEN** ретривер использует шаблоны/типы узлов соответствующего профиля без правки кода