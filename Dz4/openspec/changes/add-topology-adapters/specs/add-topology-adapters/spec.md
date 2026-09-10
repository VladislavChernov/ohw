# Delta Spec: add-topology-adapters

## ADDED Requirements

### Requirement: Topology Orchestrator Service (BR-1)

Отдельный сервис Topology Orchestrator (порт :8005, профиль `topology`, ADR-019)
ДОЛЖЕН читать `infra_topology.yaml` (`INFRA_TOPOLOGY_PATH`), отдавать текущую
топологию и активную карту адаптеров через REST и обслуживать runtime-переключение
адаптеров. Все эндпоинты ДОЛЖНЫ требовать `X-API-Key` (`AUTH_API_KEY`).

#### Scenario: Отдать топологию

- **GIVEN** валидный `infra_topology.yaml` и корректный `X-API-Key`
- **WHEN** `GET /api/v1/topology`
- **THEN** ответ `{version, environment, network, providers, endpoints, startup}`
  с провайдерами из yaml (база без override'ов)

#### Scenario: Неверный ключ

- **WHEN** запрос без/с неверным `X-API-Key`
- **THEN** ответ 401, тело не отдаётся

### Requirement: Adapters Management API (BR-2)

`GET /api/v1/config/adapters` ДОЛЖЕН возвращать эффективную карту
`{revision, adapters: {graph_store, vector_store, embeddings, reranker, llm}}`
(база из yaml + override'ы). `PUT /api/v1/config/adapters` ДОЛЖЕН обновлять на лету:
слоты валидируются по каталогу реализованных провайдеров, реактивные в записи в
SQLite, `revision` монотонно растёт; незнакомый слот/провайдер → 422.
`GET /api/v1/config/adapters/available` ДОЛЖЕН отдавать только реализованные
провайдеры (каталог фабрики, а не прожект-значения SSOT).

#### Scenario: Прочитать карту

- **GIVEN** стек с профилем `topology`
- **WHEN** `GET /api/v1/config/adapters`
- **THEN** `adapters` совпадает с `providers` из `infra_topology.yaml`
  (слоты canonical), `revision` — целое

#### Scenario: Переключить адаптер на лету

- **GIVEN** активная карта с `vector_store: neo4j`
- **WHEN** `PUT /api/v1/config/adapters` с body `{"vector_store": "inmemory"}`
- **THEN** ответ 200 `{revision: +1, adapters: {…vector_store: "inmemory"…}}`;
  повторный `GET` сохраняет значение после рестарта сервиса (SQLite)

#### Scenario: Неизвестный провайдер

- **WHEN** `PUT /api/v1/config/adapters` со слотом провайдером вне каталога
- **THEN** ответ 422, карта не меняется (revision тот же)

### Requirement: Потребление карты и hot-reload (BR-3)

Если задана `TOPOLOGY_URL`, Query Worker ДОЛЖЕН строить провайдеры по карте
адаптеров из топологии (slot-resolution: явный слот карты > env > дефолт) и ДОЛЖЕН
подхватывать изменение карты «на лету»: при смене `revision` pipeline пересобирается
без рестарта контейнера (опрос интервалом `TOPOLOGY_POLL_INTERVAL`, дефолт 5 с).
При `TOPOLOGY_POLL_INTERVAL=0` или недоступной топологии поведение ДОЛЖНО
оставаться как в M2 (один pipeline, env-фабрика).

#### Scenario: Hot-reload при смене карты

- **GIVEN** запущенный worker c `TOPOLOGY_URL`
- **WHEN** `PUT /api/v1/config/adapters` меняет слот
- **THEN** на ближайшей итерации опроса revision вырос, worker пересобирает
  `QueryPipeline` с новым провайдером; следующий запрос использует новую карту

#### Scenario: Обратная совместимость без топологии

- **GIVEN** worker без `TOPOLOGY_URL` (или топология недоступна, или интервал 0)
- **THEN** pipeline строится один раз из env; опрос не ведётся

### Requirement: Каталог и совместимость (BR-4, BR-5)

Каталог `available` ДОЛЖЕН содержать только реализованные провайдеры:
`graph_store: [neo4j, inmemory]`, `vector_store: [neo4j, inmemory]`,
`embeddings: [deterministic]`, `reranker: [noop]`, `llm: [openai, fake]`.
Бандл НЕ ДОЛЖЕН менять контракты ABC-адаптеров, маршруты Query/Ingestion/Config API,
события ADR-016 и SSOT `namespaces.yaml` (namespace `adapters` остаётся эталоном
в target-значениями). `uv run pytest -q`, `ruff`, `mypy` остаются зелёными;
e2e-маркер не затрагивается.