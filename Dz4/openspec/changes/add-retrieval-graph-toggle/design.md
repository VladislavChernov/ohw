# Design: add-retrieval-graph-toggle

## Предмет закономерности

M2 QueryPipeline (`src/graphrag_proto/retrieval/pipeline.py`) исполняет оси поиска
параллельно (ThreadPoolExecutor, max_workers=2):

```python
emit("status", {"stage": "graph"})
emit("status", {"stage": "vector"})
with ThreadPoolExecutor(max_workers=2) as pool:
    future_graph = pool.submit(graph_retriever.retrieve, query)
    future_vector = pool.submit(vector_retriever.retrieve, embedding)
    skeleton_rows = future_graph.result()
    body_chunks = future_vector.result()
```

Тумблер вводится на уровне «формируется ли graph-фьючер вообще», а не внутри
`GraphRetriever` — ось не создаёт даже попытки обращения к graph store
(в т.ч. эмитится без грехов пустого CYPHER-прогона).

## Порядок резолва флага

`graph_search_enabled`:

1. env `RETRIEVAL_GRAPH_ENABLED` (строка `true|false`, регистронезависимо) — при
   наличии перекрывает всё (A/B без правок YAML);
2. иначе `profile["retrieval"].get("graph_search_enabled", True)` (профили доменов
   получают явное `graph_search_enabled: true`);
3. отсутствие ключа — считаем `True` (текущее поведение M2 не меняется).

Резолв — в `QueryPipeline.run()` после `_load_profile` (профиль уже есть к этому
моменту). Перед `pool.submit` на graph: если `enabled == False` → `skeleton_rows = []`,
`emit("status", {"stage": "graph", "enabled": False})`, фьючер не создаётся; в пуле
остаётся только векторный сабмит.

## Контракт и совместимость

- `GraphStoreProvider.query`/`VectorStoreProvider` НЕ меняются.
- События ADR-016 дополняются аддитивно: `status` получает опциональное поле
  `payload.enabled` (только для `stage:"graph"`); `done.payload` — `retrieval_time_s`,
  `total_time_s`. Старые потребители (UI `_event_body`) игнорируют неизвестные поля;
  новые тайминги отображаются в UI (задача 2.2) без изменения формата.
- `retrieval_time_s` считается от старта осей (после embedding) до конца ContextAssembly
  (`time.monotonic`, как уже принято для `generation_time_s`);
  `total_time_s` — от начала `run()` до завершения LLM (включая `generation_time_s`).

## Побочные эффекты

- При `enabled: false` статусы стрима: `embedding → graph{enabled:false} → vector →
  rerank → llm → done` (набор этапов UI не меняется, «graph» остаётся в чеклисте
  стадий — UI их показывает строками; при желании можно серым — не требуется).
- Контекст без скелета: `ContextAssembly.assemble([], body_chunks)` — сумма корпус `body`
  (пустой скелет — уже штатный сценарий для доменов без `node_labels`, ничего нового).

## Реализуемость

Изменения локализованы в 4-5 файлах + тесты + docs; контракты ядра не трогаются,
инструменты проверки (ruff/mypy) не пострадают.