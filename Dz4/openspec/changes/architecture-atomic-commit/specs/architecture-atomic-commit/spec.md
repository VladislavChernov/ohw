# Delta Spec: честный контракт атомарности COMMIT (A-2)

## Scope

Правки контрактов адаптерного слоя и Ingestion COMMIT для закрытия замечания A-2
(`review_team.md:40-42`). Ядро остаётся вендор-независимым; никакого 2PC и хардкода
Cypher в ядре.

## 1. Capability `consistency_capability()`

`src/graphrag_proto/retrieval/adapters/base.py`:

```python
class GraphStoreProvider(ABC):
    def consistency_capability(self) -> str: ...      # default "best_effort"
    def engine_key(self) -> str | None: ...           # default None (разные движки)

class VectorStoreProvider(ABC):
    def consistency_capability(self) -> str: ...      # default "best_effort"
    def engine_key(self) -> str | None: ...           # default None
```

- `"atomic"` — ось может участвовать в атомарной записи пары **только если обе оси
  этого провайдера обслуживаются общим движком** (`engine_key()` совпадают).
- `"best_effort"` — дефолт; атомарность пары не обещается.

## 2. Стратегия CommitStage

`ingestion_service/pipeline/orchestrator.py::CommitStage._write`:

- **atomic-пара**: `g.consistency_capability() == v.consistency_capability() == "atomic"`
  и `g.engine_key() == v.engine_key()` → запись обеих осей в одной транзакции движка
  (для Neo4j-пары — один `session`; для InMemory-пары — общий журнал).
- **иначе (best_effort)**: commit графа → commit вектора; при сбое второй оси —
  компенсация первой существующими операциями удаления (`delete_node`/`delete_vectors`
  по id из плана записи); джоба завершается `failed` с пометкой «компенсировано».
- Повторный прогон джобы после компенсации idempotentен (L2-06): контент не изменился —
  registry no-op.

## 3. Инварианты

- **L2-04** (уточнение): «Запись узлов/рёбер и эмбеддингов коммитится атомарно
  в пределах одного движка (pair `atomic`); разнородные пары — best-effort с
  компенсацией». Прототип-требования §1.2 — «без частичных записей» переформулируется.
- **SSOT адаптеров**: декларации `namespaces.yaml` (namespace `adapters`) по
  capabilities совпадают с реализацией (`test_adapter_catalog_ssot.py` расширяется).

## 4. Не входит

- Распределённая транзакция/2PC — явно запрещено (ADR-024).
- Entry-point-регистрация провайдеров БД («БД как плагины») — идея зафиксирована в
  `design.md`, реализуется отдельным решением.

## Проверка приемлемости

- `pytest` зелёно, `ruff`/`mypy` чисто.
- Пара inmemory+inmemory → одна транзакция (не компенсация); пара inmemory+фейк-neo4j —
  сбой второй оси → компенсация первой, джоба failed с пометкой.
- Ingestion на стеке (по умолчанию inmemory) — успех, повторная загрузка — no-op.