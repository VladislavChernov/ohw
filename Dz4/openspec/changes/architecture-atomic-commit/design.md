# Дизайн: capability `consistency` и стратегия COMMIT

## Текущее состояние

`CommitStage._write` (`ingestion_service/pipeline/orchestrator.py:345`) открывает два
независимых контекста транзакций:

```python
with self._graph_store.transaction() as graph_tx, self._vector_store.transaction() as vector_tx:
    graph_tx.upsert_nodes(nodes); graph_tx.upsert_edges(edges)
    vector_tx.upsert_vectors(vectors)
```

На Neo4j это два `session.begin_transaction()` (`neo4j.py:147`, `:288`) на разных
сессиях: commit не атомарен между сессиями. `InMemory*` — общий журнал внутри процесса,
там «атомарность» есть фактически (одна нить), но контракт это не различает.

## Контракт capability

```python
# retrieval/adapters/base.py
Consistency = Literal["atomic", "best_effort"]  # нет типовой алгебры: ровно два значения

class GraphStoreProvider(ABC):
    def consistency_capability(self) -> str:
        """`"atomic"` — ось может участвовать в атомарной записи пары (общий движок);
        иначе `"best_effort"` (по умолчанию). Ядро доверяет декларации, не проверяет вендора."""
        return "best_effort"
```

Правила:

| Провайдер | capacity | Условие |
|---|---|---|
| `InMemoryGraphStore`/`InMemoryVectorStore` | `atomic` | всегда (один процесс/журнал) |
| `Neo4jGraphStore`/`Neo4jVectorStore` | `atomic` | общий драйвер/URL одной пары (слот каталога `neo4j`) |
| прочие/сторонние | `best_effort` | дефолт ABC |

Пары, которые ядро считает атомарными, определяются на уровне стратегии CommitStage:

- `(g, v)`: `g.consistency_capability() == v.consistency_capability() == "atomic"`
  **и** обе оси обслуживаются общим движком (см. `graph_store.engine_key() == vector_store.engine_key()`).
  Исключаем ложную «атомарность» разнородных движков.
- Любая другая пара → `best_effort`.

`engine_key()` — новый контрактный метод (например фрагмент URL/имя инстанса БД),
используется только для сравнения пары, ядро не разбирает его формат.

## Стратегия CommitStage

### best_effort (default)

```
1. нарисовать план: что писать (nodes/edges/vectors) — уже есть в _write
2. graph_ok = commit(граф); если сбой → джоба failed, ничего компенсировать не надо
3. vector_ok = commit(вектор); если сбой → компенсация graph (удаление записанного:
   delete_node(chunk_id)+delete_edges, остаются source/entities) → джоба failed
   с пометкой «компенсировано»
4. оба ок → done
```

Компенсация использует существующие операции провайдеров (`delete_node`, `delete_vectors`),
идентификаторы — из плана (source_id, chunk_id), так что никакой доп. инфраструктуры.
Повтор той же джобы idempotentен: registry уже знает версию, контент не изменился → no-op.

### atomic

Для пар с общим движком — запись обеих осей в **одной транзакции движка**:
один `session.begin_transaction()`, оба набора операций выполняются и коммитятся вместе.
В прототипе путь реализуется контрактно (для пары Neo4j — один session; для InMemory —
общий журнал уже является единой транзакцией). Никакого 2PC: два движка не имеют общего
координата — это фиксируется в ADR-024 и НЕ реализуется.

## Состояние топологии/SSOT

`namespaces.yaml` (namespace `adapters`) получает декларацию capabilities (кто за что
отвечает: id адаптера → consistency). SSOT-тест `tests/test_adapter_catalog_ssot.py`
проверяет, что декларация YAML совпадает с фактическим `consistency_capability()`
реализаций (как это уже сделано для слотов). `infra_topology.yaml` — пометка, что пара
активных провайдеров определяет стратегию автоматически.

## Документация

- **ADR-024**: «Атомарность COMMIT ограничена одним движком» — отказ от 2PC,
  capability-контракт, компенсация как паттерн; когда оператор обязан выбирать
  `atomic` (загрузка в один инстанс Neo4j).
- **L2-04** переформулируется: «атомарность COMMIT гарантируется в пределах одного
  движка (pair `atomic`); разнородные пары — best-effort с компенсацией».
- **§2.6 adaptable_specification** дополняется capability-контрактом.

## Идея «БД как плагины» (зафиксировать как тему для обсуждения)

> Требование пользователя: пометить идею организовать работу с БД как с плагинами.
> Механизм плагинов уже есть (entry_points, `docs/adapters_guide.md`, `chunkers_guide.md`);
> провайдеры БД (`GraphStoreProvider`/`VectorStoreProvider`) при этом пока регистрируются
> фабрикой/каталогом, а не entry-point-контрактом.

Тезисы (реализуются вне этого бандла):
1. Сторонние БД-провайдеры — через setuptools `entry_points` (как чанкеры), не правкой
   каталога ядра; каждый провайдер несёт capability-метаданные (`consistency`, id движка).
2. Каталог/SSOT остаётся единственным источником истины для активной карты; entry-point —
   механизм регистрации доступного.
3. Вопросы на обсуждение: способно ли ядро выбирать стратегию по capability автоматически
   (остаётся ли решение у оператора), как синхронизировать YAML и entry-point-регистр,
   что даёт это для T2 («сбой под нагрузкой») и для multi-engine в будущем.

Статус: **зафиксировано как идея; требует отдельного решения/обсуждения.**