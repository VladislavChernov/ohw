# Design: Proposal Review Gate

## 1. Граница

`Proposals/` — входной каталог идей. Proposal не меняет `CONCEPT.md`, `docs/`, runtime,
OpenSpec-контракт или eval-гейт до завершения review. Решение фиксируется отдельно от текста
предложения.

## 2. Триггеры и периодичность

Базовый review gate проходит после стабилизации текущего lightweight baseline и до
`M6-Growth / pre-connectors`. Дополнительно gate назначается после вехи, которая меняет
retrieval, storage, lifecycle, data model или evaluation protocol.

Периодический review не превращает каждое предложение в обязательную задачу. В очередь
попадают только предложения с владельцем, проблемой, предполагаемым выигрышем и хотя бы
одним проверяемым критерием.

## 3. Вход и evidence matrix

Для каждого proposal заполняются поля:

- problem и target user/workload;
- current-state baseline и существующие ограничения;
- предлагаемая delta и зона владения;
- ожидаемая польза и способ измерения;
- влияние на ingest/offline/query runtime;
- данные, provenance, identity, revision и invalidation;
- инфраструктура, latency, RAM, стоимость и эксплуатация;
- security/ACL/domain isolation;
- альтернативы и partial-scope;
- open questions и неизвестные.

Evidence разрешено ссылаться на действующие документы, тестовые отчёты, paired eval,
профили и операционные наблюдения. Marketing-style утверждения без измеримого критерия
получают статус `deferred`, а не считаются доказанными.

## 4. Решения

| Решение | Что означает | Обязательный follow-up |
|---|---|---|
| `accepted` | идея целиком совместима и готова к контракту | новый OpenSpec change, ADR при необходимости, тесты/eval |
| `partial` | принимается только ограниченная часть | зафиксировать accepted/rejected slices и отдельный change на accepted part |
| `deferred` | недостаточно данных или prerequisites | записать условие повторного review и измеримый gap |
| `rejected` | идея не подходит для текущего product/architecture | сохранить причину, альтернативу и условия возврата в очередь |
| `superseded` | proposal заменён более поздним решением | ссылка на replacement |

Review не выбирает между «красивым описанием» и реализацией по вкусу: решение должно
опираться на evidence и ограничения.

## 5. Review-record

Для каждого решения создаётся запись в review-каталоге change-бандла:

```text
review-record:
  proposal:
  reviewer/date:
  decision: accepted | partial | deferred | rejected | superseded
  accepted_scope:
  rejected_scope:
  evidence:
  assumptions:
  risks:
  follow_up_change:
  revisit_when:
```

Review-record — источник статуса proposal, но не заменяет OpenSpec для принятой части.

## 6. Первый pilot: Graph as Data Map

`Proposals/graph_as_data_map_manifest.md` рассматривается в три независимых слоя:

1. **Data Map / metadata-only:** offline graph формирует bounded passport, runtime — vector-only.
2. **Graph expansion experiment:** готовая projection расширяет vector seeds, включается только
   в paired experiment.
3. **Background mining:** mined/derived tags и link prediction — отдельный future scope.

Нельзя принимать документ целиком по одному из слоёв: review должен явно указать, какие
claims подтверждены, какие являются гипотезами, а какие требуют отказа или измерения.

## 7. Граница ответственности

Review phase не запускает live Neo4j, не меняет production projection и не считается
реализацией. Принятая часть переходит в отдельный spec-first change; код и тесты начинаются
только после завершения её собственного DoD.
