# Delta Spec: add-retrieval-graph-toggle

## ADDED Requirements

### Requirement: Граф-тумблер (BR-1, BR-2)

QueryPipeline ДОЛЖЕН читать конфигурацию графовой оси перед выполнением ретрива:
значение `graph_search_enabled` — из секции `retrieval` активного Domain Profile
(дефолт `true`), с приоритетом env-переменной `RETRIEVAL_GRAPH_ENABLED=true|false`.
При `false` графовая ось НЕ ДОЛЖНА исполняться: не создаётся запрос к graph store,
`status {"stage":"graph"}` НЕ ДОЛЖЕН скрываться, а ДОЛЖЕН эмититься с
`payload.enabled: false`, скелет контекста пуст, контекст собирается из векторного блока.

#### Scenario: Графовая ось включена (по умолчанию)

- **GIVEN** `graph_search_enabled: true` в профиле (или отсутствие ключа) и нет env-override
- **WHEN** выполняется query
- **THEN** GraphRetriever запускается параллельно с векторной осью, `status` для
  `stage: "graph"` содержит `enabled: true`

#### Scenario: Графовая ось выключена

- **GIVEN** `graph_search_enabled: false` в профиле (или `RETRIEVAL_GRAPH_ENABLED=false`)
- **WHEN** выполняется query
- **THEN** graph store не вызывается, `status {"stage":"graph"}` приходит с
  `enabled: false`, `skeleton` пуст, pipeline доходит до `done`

#### Scenario: Env-override перекрывает профиль

- **GIVEN** в профиле `graph_search_enabled: true`, а `RETRIEVAL_GRAPH_ENABLED=false`
- **WHEN** выполняется query
- **THEN** графовая ось пропускается (env имеет приоритет)

### Requirement: Тайминги ретрива (BR-3, BR-4)

Конверт `done` (ADR-016) ДОЛЖЕН дополниться аддитивными полями
`retrieval_time_s` (граф ∥ вектор + rerank + Context Assembly) и `total_time_s`
(весь pipeline, включая генерацию); `generation_time_s` сохраняется без изменения
семантики. Демо-UI ДОЛЖЕН отображать новые тайминги в итоге запроса.
Старые потребители конверта ДОЛЖНЫ продолжать работать (поля опциональны).

#### Scenario: Время ретрива в ответе

- **GIVEN** успешный `done` с `generation_time_s`
- **THEN** `done` также содержит `retrieval_time_s` и `total_time_s` (вещественные, >= 0),
  `retrieval_time_s` < `total_time_s` при ненулевой генерации

#### Scenario: Отображение в UI

- **WHEN** пользователь получает ответ во вкладке «Запросы»
- **THEN** рядом с `generation_time_s` отображаются `retrieval_time_s` и `total_time_s`

### Requirement: Совместимость (BR-5)

Бандл НЕ ДОЛЖЕН менять контракты `GraphStoreProvider`/`VectorStoreProvider`,
маршруты Query/Ingestion/Config API и SSE-очерёдность событий; единственные изменения
конверта — новые опциональные поля. `uv run pytest -q`, `ruff`, `mypy` остаются зелёными.