# Design: Semantic Cache (бандл 3/3 вехи M3)

## Размещение

Кэш встраивается в `QueryPipeline.run` **сразу после** `self._embedder.embed(...)`,
до profile/retrievers. Причина: эмбеддинг запроса уже посчитан и является ключом
и для векторной оси, и для кэша (общая ось L2-04); переиспользуем его — hit не
платит даже за ретрайв.

```
POST /query → queue → worker.claim → pipeline.run(query, domain, emit)
                                       │
                 embed(query) ─────────┤
                 │                      │
                 │  semantic_cache?     │
                 │   │  lookup() ── hit → emit(status:cache{hit:true}) → emit(done{cache_hit:true})
                 │   │  miss ──────────────► graph∥vector → rerank → context → LLM
                 │   └  store(done.text/sources) ────────────────────────────► emit(done)
```

Кэш-объект создаётся в `runtime.build_pipeline` и **переживает hot-reload**:
`worker.set_pipeline(build_pipeline(adapter_map=...))` получает тот же инстанс
(передаётся как отдельный параметр, не живёт в карте адаптеров).

## Контракт

```python
class CachedAnswer:            # то, что вернёт hit
    text: str
    sources: list[dict[str, float | str]]  # {source_url, relevance}

class SemanticCache(ABC):
    mode: str                  # "inmemory" | "redis"
    def lookup(self, embedding: list[float], threshold: float) -> CachedAnswer | None: ...
    def store(self, embedding: list[float], answer: CachedAnswer) -> None: ...
    def stats(self) -> dict[str, int]: ...   # {entries, hits, misses}
    def clear(self) -> None: ...
```

Hit: `max cosine(embedding, stored_embedding) >= threshold` (по живому TTL).
Косинус — метрика по умолчанию (эмбеддинги L2-нормализованы → косинус = dot).

## Redis-схема

- lazy `import redis`; `Redis.from_url(QUERY_REDIS_URL, decode_responses=True)`,
  дефолт `redis://valkey:6379/0` (общий с очередью ADR-023).
- Ключ: `query:sc:<domain>`, тип `HASH`.
- Поле: `sc:<sha256(repr(embedding))[:12]>`.
- Значение: JSON `{"embedding": [...], "text": "...", "sources": [...], "ts": <epoch_s>}`.
- `lookup`: `HGETALL` + линейный скан + косинус; просроченные — `lazy HDEL`.
- Ограничение масштаба: прототипный объём мал, скан допустим (документируем).

## Event-последовательность (SSE, конверт ADR-016)

- miss: как сейчас (embedding → graph/vector → rerank → llm → done), плюс
  `store()` до/после `emit("done")`, `done.cache_hit = false`,
  `done.cache_lookup_s` — время lookup.
- hit: `status{stage:"embedding"}` → `status{stage:"cache", hit:true}` →
  `done {text, sources(cached), cache_hit:true, cache_lookup_s,
  generation_time_s:0, retrieval_time_s:0, total_time_s}`. Никаких `token`.

## Свежесть: принятое решение (2026-09-13)

Остаёмся на **TTL + ручной сброс** как зафиксированном допущении, плюс два дополнения:

1. **Не кэшируем «плохие» ответы.** `store()` — no-op для пустого `text`, явного
   отказа LLM и сбоя пайплайна. Кэш отдаёт только полноценные ответы, а не ошибки
   и отказы (стоимость — пара строк, закрывает самые неприятные кейсы практики).
2. **Фиксируем «planned upgrade path».** Если понадобится точная свежесть:
   `SemanticCache` получает ревизию данных как часть ключа — префикс
   `query:sc:<rev>:<domain>`. Ingestion после фактического COMMIT
   (`created_new=True`) увеличивает счётчик `query:sc:rev:<domain>` в том же Valkey;
   lookup читает текущую rev, старые записи автоматически вне поиска, TTL их
   дочищает. Доступной остаётся и инвалидация по источникам (источник → записи
   через `sources`), но она не нужна прототипу (обратный индекс — избыточность).

### Моменты пересмотра допущения (триггеры в дорожной карте)

| Триггер | Что ломает допущение | Когда |
|---|---|---|
| **M4 — Eval** | Eval-срез обязан быть детерминированным: кэш в прогоне **off**; если захотим валидировать «живой кэш» — решать модель ревизии | при старте M4 |
| **M5 — конфигуратор профилей** | Правка domain-profile меняет Context Assembly/онтологию → закэшированные ответы старого профиля устаревают | при старте M5 |
| **M6 — коннекторы** | Постоянно обновляемые данные: окно «≤ TTL» становится бизнес-критичным | до реализации коннекторов |

Решение «глобальная vs по-доменная» ревизия — общее для всего query-контура (не только
кэша): точка принятия — де-факто перед M5/M6; в бандле выносится в задачи M4/M5 как
явный пункт пересмотра.

### Допущение, документируемое в spec/runbook

`SEMANTIC_CACHE_TTL_S` + `clear()` (`del query:sc:*`) — единственные механизмы
свежести в этом бандле. Фиксируем явно (стиль ADR-021: допущение вместо тихой
деградации), а не как скрытый минус.

## Файлы

- `prototype/src/graphrag_proto/retrieval/semantic_cache.py` — ABC + 2 реализации
- `prototype/src/graphrag_proto/retrieval/pipeline.py` — интеграция
- `prototype/src/graphrag_proto/query_service/runtime.py` — сборка из env
- `prototype/src/graphrag_proto/query_service/worker.py` — main() проброс
- `prototype/tests/test_semantic_cache.py`, `test_retrieval_pipeline.py`,
  `test_worker_hotreload.py` — новые/расширенные тесты