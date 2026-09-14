# Задачи

## 1. Capability-контракт `consistency`

- [x] 1.1. `retrieval/adapters/base.py`: на `GraphStoreProvider`/`VectorStoreProvider`
      — `consistency_capability() -> str` (`"atomic" | "best_effort"`), дефолт
      `"best_effort"`; docstring-контракт: `atomic` — обе оси на одном движке,
      единый транзакционный координатор.
- [x] 1.2. Реализация capability в провайдерах: `Neo4jGraphStore`/`Neo4jVectorStore` —
      `atomic` только при общем инстансе/URL (оба слота каталога = `neo4j` и общий
      `GRAPH_STORE_URL`/провайдер), иначе `best_effort`; `InMemory*` — `atomic`
      (общий журнал транзакций внутри процесса).
- [x] 1.3. `namespaces.yaml` (namespace `adapters`) — слот/значения capability в SSOT;
      контрактный тест `tests/test_adapter_catalog_ssot.py` расширяется на capabilities.

## 2. Стратегия CommitStage

- [x] 2.1. `ingestion_service/pipeline/orchestrator.py::CommitStage._write`: выбор
      стратегии по паре `(graph.consistency_capability, vector.consistency_capability)`:
      оба `atomic` и общий движок → atomic-путь (одна транзакция движка, запись обеих
      осей); иначе → best_effort.
- [x] 2.2. Atomic-путь: операция записи узлов/рёбер+векторов в одной транзакции
      координатора (для Neo4j-pair — один `session`/`begin_transaction`; в текущем
      прототипе покрывается контрактно, без запросов-Cypher в ядре).
- [x] 2.3. Best-effort-путь: commit graph, затем commit vector; при сбое второй оси —
      компенсация первой (удаление записанного через существующие операции
      `delete_node`/`delete_vectors` по DocumentRegistry), ошибка в статус джобы с
      пометкой «компенсировано».

## 3. ADR и документация

- [x] 3.1. `docs/05_adr_log.md`: ADR-024 (черновик) — решение: никакого 2PC; атомарность
      только внутри одного движка (`atomic`), разнородные пары — `best_effort` +
      компенсация; когда capability обязана быть `atomic` (загрузка в один и тот же
      инстанс).
- [x] 3.2. `docs/invariants.md` L2-04 — формулировка уточняется
      («атомарно в пределах одного движка; вне — best-effort + компенсация»);
      `docs/adapters_specification.md` §2.6.1 — контракт `consistency_capability`;
      §1.2 `docs/prototype_requirements.md` — строки «без частичных записей» уточняются.
- [x] 3.3. Пометка идеи «БД как плагины» в `design.md` документация-раздела
      (или `docs/adapters_guide.md`) — тезис «провайдеры БД как entry-point-плагины
      с capability-метаданными»; явная пометка «требует обсуждения, не реализуется
      в этом бандле».

## 4. Тесты

- [x] 4.1. Unit (best_effort): сбой второй оси в `CommitStage` → компенсация первой
      вызвана, состояние осей согласовано (ident-тесты на couple inmemory/fake).
- [x] 4.2. Unit (atomic): пара InMemory+InMemory — одна транзакция, без компенсации;
      `consistency_capability()` отражается в каталоге/SSOT.
- [x] 4.3. Идемпотентность: повторный прогон джобы после компенсации — консистентный
      результат, версия не растёт без изменения контента (L2-06).
- [x] 4.4. `uv run pytest -q` зелёно; `uv run ruff check .`, `uv run mypy src` чисто.

## 5. Приёмка

- [x] 5.1. Live-прогон ingestion (inmemory-индекс, профиль по умолчанию): успех,
      повторная загрузка — no-op (закрыто e2e `tests/test_ingestion_api.py::test_idempotent_noop_on_same_content`
      и `tests/test_demo_e2e.py`, полный прогон — 243 passed).
- [x] 5.2. Reviewer-прогон бандла (self-review) + правки по findings (2 BUG, 4 RISK/NIT
      закрыты) + история (`docs/history.md`, Этап 10); 243 теста, ruff/mypy чисто.
      Коммит — по подтверждению пользователя.
