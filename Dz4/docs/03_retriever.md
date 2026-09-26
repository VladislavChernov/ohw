# Документация: Стратегия Ретривера и слияния контекста

> **Статус:** vector-only baseline + optional graph experiment

## 1. Baseline и graph experiment

### Baseline

При запросе:

1. Semantic cache (если включён) проверяет revision/domain.
2. Embedder вычисляет embedding запроса.
3. **Vector Retriever** возвращает top-K chunks с `text`, `source_id`, `chunk_id`, `domain`,
   `revision` и optional `context_ids`/`tag_ids`.
4. Reranker/Context Assembly формируют bounded prompt и provenance-aware sources.
5. LLM генерирует ответ, API возвращает sources и timings.

Baseline не вызывает graph adapter и не зависит от наличия graph projection.

### Graph experiment

Если projection построена inline или offline job и её revision готова, после vector search
выполняется bounded expansion по `context_ids`/`tag_ids` из metadata. Expansion имеет depth,
fanout, total-node и time budget, возвращает path/depth/confidence и получает ограниченный boost.

`expand()` не ограничивает обход видом ребра: по умолчанию идёт в обе стороны по любому типу и
возвращает фактический вид последнего прыжка в поле `kind`. Направление задаётся
`retrieval.expansion_direction` и принимает `both` (по умолчанию), `out` или `in`; прежние значения
`parent` и `related` сохраняются как синонимы `out` и `in`. Необязательное сужение
`retrieval.expansion_kinds` ограничивает обход явным списком видов; пустое значение и `any`
означают отсутствие сужения. Глубина, fanout и общее число узлов ограничены независимо от видов.
Пустой результат помечается `degraded` с причиной `empty_projection` (рёбер нет) либо
`no_matching_edge_kinds` (сужение ничего не нашло), и запрос silently уходит в vector-only.
Контракт обхода и история дефекта — `data_model.md` §3.1.

Graph experiment не является prerequisite для baseline. Перед expansion QueryPipeline проверяет
`ProjectionState`: `ready` и актуальная `data_revision` разрешают graph; `pending`, `degraded`,
`stale`, `failed` или отсутствие state дают vector-only fallback. При `graph_enabled=false`,
отсутствии adapter, неполной или stale projection retrieval возвращает vector-only fallback.

## 2. Fallback и атрибуция

Fallback получает явный degraded marker. В graph experiment такой fallback не считается успешным
graph-enabled результатом.

`qa_log`/`trace` сохраняют seed chunk IDs, graph paths, depth, edge kind, confidence, boost,
исходный vector score, `projection_status` и projection revision. Semantic cache разделяет
vector baseline и graph experiment по mode, а для готовой graph projection добавляет
`projection_revision` в cache key. Fallback и state `missing/stale/degraded/failed` не кэшируются
как успешный graph result. Это позволяет отличить реальный вклад experiment от graph-first
parallel search или улучшения reranker.

## 3. Context budget

Vector body и optional graph evidence имеют отдельные бюджеты. При переполнении сначала
ограничивается expansion, затем вытесняются наименее полезные vector chunks по reranker score.
Graph evidence не вставляется отдельным бесконечным skeleton-блоком.

## 4. Совместимость

Graph и vector остаются независимыми adapters. Neo4j — выбранный backend прототипа, но не
обязательная архитектурная привязка; S3/embedded/другой graph adapter реализует тот же
bounded expansion contract. Если vector backend уже умеет нужные metadata filters и связи,
отдельный graph backend можно не подключать.
