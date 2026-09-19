# Дизайн: политика конкурентной записи COMMIT (retry transient + детерминированный порядок)

## Текущее состояние (факт, 2026-09-16)

Запись производится в `CommitStage` (`ingestion_service/pipeline/orchestrator.py`):

- **Атомарная пара** (`_write_atomic`, `:396-417`): `graph.atomic_batch()` → один
  `session.begin_transaction()` движка (`neo4j.py:127-133`).
- **best_effort** (`_write_best_effort`, `:419-459`): `graph.transaction()` → `vector.transaction()`,
  при сбое второй оси — компенсация `delete_node`/`delete_vectors`.
- **soft_delete_source** (`:462-481`): та же двухитовая запись.

Сами операции — поштучные Cypher-запросы в одной транзакции:

- `_upsert_nodes` (`neo4j.py:220-229`): `MERGE (n {node_id: $node_id}) SET n:Labels SET n += $props`
  — цикл по `nodes`, порядок из плана.
- `_upsert_edges` (`neo4j.py:232-244`): `MATCH (a{node_id}) MATCH (b{node_id}) MERGE (a)-[:T]->(b)`
  — цикл по `edges`.
- vector-ось (`_upsert_vectors`, `:347-...`): MERGE по `chunk_id`/`node_id`.

Проседание именно такое: две джобы нормализуют общие сущности (`ent:{domain}:{canonical_name}`),
и обе выполняют `MERGE` этих узлов внутри своих транзакций. Если замки захватываются в
разном порядке — Neo4j отменяет одну транзакцию с `Neo.TransientError.Transaction.DeadlockDetected`
(transient-класс, безопасен для повтора).

Ключевое наблюдение: **план записи детерминирован по составу**, но не по порядку — порядок
`nodes`/`edges` сейчас определяется порядком сборки в `CommitStage._write` (source → entities →
chunks), а значит потенциально различается между джобами при одинаковой логике обхода.

## Изменения

### 1. Контракт transient-retry на провайдерах (паттерн ADR-024)

`retrieval/adapters/base.py`:

```python
class GraphStoreProvider(ABC):
    def transient_aware(self) -> bool:
        """True — хранилище может бросать transient-ошибки (сеть/deadlock),
        которые имеют смысл ретраить. InMemory — False, Neo4j (и сетевые) — True."""
        return False

    def is_transient(self, exc: BaseException) -> bool:
        """Классификация ошибки как повторимой. Вызывается только если transient_aware().
        Ядро не импортирует вендорские пакеты (L1-02) — ответственность на провайдере."""
        return False
```

`Neo4jGraphStore`/`Neo4jVectorStore`: `transient_aware()` → True;
`is_transient(exc)` → `isinstance(exc, ClusteredLockError) | isinstance(exc, TransientError)`
из `neo4j.exceptions` (импорт в файле адаптера — допустимо, это граница вендора, ADR-012).
`InMemory*`: дефолт ABC (False).

> Альтернатива «магическое имя класса в ядре» (хватать `exc.__class__.__module__ == "neo4j..."`)
> отклонена: ломает L1-02 (ядро узнает вендора) и ломко. Контрактный способ — декларация.

### 2. Retry-loop в CommitStage

Общая функция `_with_commit_retry(stores, fn)` в `orchestrator.py`:

```
attempts = N_RETRY_COMMIT + 1          # дефолт N_RETRY_COMMIT=3
delay = RETRY_BASE_S                    # 0.2
for i in range(attempts):
    try:
        return fn()
    except BaseException as exc:
        transient = any(s.transient_aware() and s.is_transient(exc)
                        for s in stores if hasattr(s, "transient_aware"))
        if not transient or i == attempts - 1:
            raise
        time.sleep(delay + random.uniform(0, RETRY_JITTER_S))   # 0.1
        delay *= 2
```

- Применяется к `_write_atomic`, `_write_best_effort` и телу soft-delete.
- **Компенсация в best_effort — только после исчерпания retry**: transient-ошибка второй оси
  не должна сразу каскадить удаление графа; сначала след повторов. Поэтому retry-loop
  оборачивает `_write_best_effort` целиком, а не каждую ось по отдельности (повтор повторит
  и графную запись — она идемпотентна по L2-06).
- Не-transient ошибки (валидация, constraint) — без повторов, `failed` немедленно.
- По факту уникальные transient-причины логируются с номером попытки (наблюдаемость,
  L4-04 паттерн).

### 3. Детерминированный порядок записи

В `CommitStage._write` перед формированием вызовов:

```python
nodes.sort(key=lambda x: x["node_id"])
edges.sort(key=lambda e: (e["from_id"], e["to_id"], e["type"]))
```

- Поля плана контрактные (`node_id`, `from_id`, `to_id`, `type`) — ядро ничего не знает
  о движке (L1-02), сортировка чистый lexicographic, детерминированная (L1-05).
- Все транзакции берут замки в одинаковом порядке → классический цикл A→B/B→A исчезает.
- Реализация сортировки в общем виде (для GraphTx) не нужна: она в CommitStage, где план
  собирается. Адаптер принимает список как есть.
- Дополнительно (опционально, M5-хвост): переместить sort в `_upsert_nodes`/`_upsert_edges`
  как чисто контрактную гарантию порядка — защита от будущих вызовов из других мест.
  Решение: делать и там — двойная страховка дешёвая.

### 4. Re-injest/soft-delete

`soft_delete_source` тоже оборачивается в retry-loop (transient при delete_vectors). Порядок
удаления чанков не критичен по тем же причинам (DELETE по id), сортировка не требуется.

## Инварианты и ADR

- **Новый инвариант** (предлагаю добавить в L3 или L2, заголовок: конкурентная запись):
  > L3-06 (черновик): «Par-транзакции с общими узлами не дают deadlock-циклов:
  > план записи COMMIT детерминирован по порядку, а transient-ошибки движка ретраятся
  > (до N_RETRY_COMMIT) перед fail/компенсацией».
  В `docs/invariants.md`.
- **ADR-028** (черновик) в `docs/05_adr_log.md`: политика конкурентной записи — retry
  transient как контрактная обязанность, детерминированный порядок, граница (не-transient
  не ретраим, 2PC не вводим).
- **`docs/06` §5**: риск deadlock (сегодня) + note, что S1 (`INGEST_MAX_CONCURRENT=1`) —
  временное ограничение, снимается S2.

## Среда/конфиг

- Env для retry: `N_RETRY_COMMIT` (дефолт 3), `RETRY_BASE_S` (0.2), `RETRY_JITTER_S` (0.1) —
  инжектируются в `CommitStage` (по умолчанию без env-чтения в юнит-тестах).
- `compose.eval.yaml`: `INGEST_MAX_CONCURRENT=1` помечается комментарием «S1-обход, снять после S2».

## Что НЕ делаем

- Single-writer очередь (S3) — отдельная проработка на стадии «Рост».
- Меняем дефолт `INGEST_MAX_CONCURRENT` сейчас: остаётся 1 в eval, 2 в общем compose (после S2).