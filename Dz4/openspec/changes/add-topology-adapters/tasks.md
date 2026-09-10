# Задачи

## 1. Каталог провайдеров и сборка по карте

- [x] 1.1. `retrieval/adapters/factory.py`: регистр слотов (`graph_store`, `vector_store`,
      `embeddings`, `reranker`, `llm`) → реализованные провайдеры;
      `build_adapters(adapter_map) -> Adapters` (dataclass), существующие `build_*`
      делегируют регистру; приоритет на уровне слота: карта > env > дефолт.
- [x] 1.2. `retrieval/adapters/topology_client.py`: класс `TopologyClient`
      (`adapters_map()`, `revision()`, `from_env()`); недоступность топологии → `None`
      (фоллбэк env); кэш revision + карты, thread-safe.

## 2. Topology Orchestrator Service (:8005)

- [x] 2.1. `topology_service/catalog.py`: каталог реализованных провайдеров
      (общий источник с фабрикой; `available` отдаёт только buildable).
- [x] 2.2. `topology_service/topology.py`: загрузка и валидация `infra_topology.yaml`
      (слоты из SSOT `namespaces.adapters`, структура providers/endpoints/startup);
      `infra_topology.yaml`: providers → реализованные id каталога (neo4j/openai/deterministic/noop).
- [x] 2.3. `topology_service/store.py`: SQLite-override'ы адаптеров (PUT) + монотонный
      `revision` (bump только при фактическом изменении).
- [x] 2.4. `topology_service/app.py` (FastAPI): `GET /api/v1/topology`,
      `GET /api/v1/config/adapters`, `PUT /api/v1/config/adapters` (валидация → 422),
      `GET /api/v1/config/adapters/available`, `/health`; проверка `X-API-Key`.
- [x] 2.5. `pyproject.toml` script `graphrag-topology`; `infra/compose.yaml`:
      сервис `topology-orchestrator` с `build:`, `command`, healthcheck,
      volume `infra_topology.yaml`, порт :8005, профиль `topology`;
      query-worker: `TOPOLOGY_URL`/`TOPOLOGY_POLL_INTERVAL` (pass-through).

## 3. Потребление карты + hot-reload

- [x] 3.1. `query_service/runtime.py`: `build_pipeline(adapter_map=None)` — карта
      топологии поверх env (слот карта > env); `None` — M2-поведение.
- [x] 3.2. `query_service/worker.py`: опрос `revision` (интервал `TOPOLOGY_POLL_INTERVAL`,
      дефолт 5 с, 0 — без hot-reload); при смене — пересборка `QueryPipeline` без
      рестарта; отсутствие топологии — как M2 (один pipeline).

## 4. Тесты

- [x] 4.1. `tests/test_factory_adapters.py`: `build_adapters` по карте для всех
      реализованных провайдеров; неизвестный провайдер → ValueError; карта > env.
- [x] 4.2. `tests/test_topology_service.py`: загрузка yaml, GET/PUT адаптеров
      (валидный/невалидный → 422), available, revision растёт, auth 401 без ключа.
- [x] 4.3. `tests/test_worker_hotreload.py`: смена revision → `QueryPipeline`
      пересобирается; без изменений — нет; топология недоступна — фоллбэк env.
- [x] 4.4. `uv run pytest -q` зелёно; `uv run ruff check .`, `uv run mypy src` чисто.

## 5. Документация и приёмка

- [x] 5.1. `docs/demo_runbook.md`: раздел «Topology: переключение адаптеров»
      (подъём с `--profile topology`, curl GET/PUT/available, что видит UI/воркер).
- [x] 5.2. `docs/04 §3` — аннотация о хосте :8005 (ADR-019); карта портов уточнена;
      `docs/00`/`glossary.md` расхождений фактов не выявлено.
- [x] 5.3. Живой прогон на стеке (профиль `topology`): GET → PUT vector_store=inmemory
      → revision вырос → воркер залогировал «пересборка pipeline без рестарта» →
      запрос прошёл (succeeded, retrieval 7.75 s); PUT с неизвестным → 422, revision.
- [ ] 5.4. `/review` бандла (reviewer-агент) + коммит + push `origin master` (после подтверждения).