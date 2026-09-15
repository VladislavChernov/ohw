# Spec: Semantic Cache

## Обзор

Хранилище готовых ответов запроса, ключованных эмбеддингом запроса. Повторный
запрос, чей эмбеддинг косинусно близок (≥ порога) к закэшированному, получает
`done` из кэша: **без** graph/vector/rerank/context/LLM и **без** `token`-событий.

## Контракт `SemanticCache`

```python
class CachedAnswer:
    text: str
    sources: list[dict[str, float | str]]

class SemanticCache(ABC):
    mode: str
    threshold: float            # порог живёт на инстансе (из env)
    ttl_s: float                # срок жизни записи (0 = без истечения)
    def lookup(self, embedding: list[float], threshold: float, domain: str = "") -> CachedAnswer | None
    def store(self, embedding: list[float], answer: CachedAnswer, domain: str = "") -> None
    def stats(self) -> dict[str, int]   # {entries, hits, misses}
    def clear(self) -> None
```

- `domain` — активный домен запроса (`""` по умолчанию); участвует в имени Redis-ключа
  `query:sc:<domain>` (и в namespace bucket'а InMemory-реализации). Расширение сигнатуры
  относительно исходного черновика дополнено на этапе реализаций с обратносовместимым
  дефолтом `""` (зафиксировано 2026-09-14).

- `lookup` возвращает ответ с **максимальным** косинусом среди живых (по TTL)
  записей, если `max_cosine >= threshold`; иначе `None`. Пустой/нулевой
  эмбеддинг → `None`.
- `threshold` — из env `SEMANTIC_CACHE_THRESHOLD`, дефолт `0.85`, диапазон
  `(0, 1]` (вне — ValueError).
- `SEMANTIC_CACHE_TTL_S` — дефолт `3600`; `0`/нечисловое → без истечения.
  `ts` — `time.time()` при `store`.

## Redis-реализация

- Ленивый импорт `redis`; клиент `Redis.from_url(QUERY_REDIS_URL,
  decode_responses=True)`, дефолт `redis://valkey:6379/0`.
- Ключ: `query:sc:<domain>` (domain = активный домен или `""`); тип `HASH`.
- Поле: `sc:<sha256(repr(embedding)).hexdigest()>` (полный digest, обновлено 2026-09-14).
- Значение: `json.dumps({"embedding": [...], "text": ..., "sources": [...], "ts": ...})`.
- Сканирование и чистка: `HGETALL` → фильтр по TTL → `HDEL` просроченных → косинус.
- Масштаб: линейный скан оправдан для прототипа.

## Интеграция в `QueryPipeline`

`__init__(..., semantic_cache: SemanticCache | None = None)` — `None` = выключено
(поведение идентично M2/M3-контуру, backward-compat).

`run()`:
1. `embed()` (как сейчас);
2. если кэш есть:
   - `answer = cache.lookup(embedding, cache.threshold, domain)`
   - **hit**: `emit("status", {"stage": "cache", "hit": True})`;
     `done = {text: answer.text, sources: answer.sources,
     cache_hit: True, cache_lookup_s: <float>, generation_time_s: 0.0,
     retrieval_time_s: 0.0, total_time_s: <float>}`;
     `emit("done", done)`; вернуть `done`. LLM/store не вызываются, `token` не шлётся.
   - **miss**: обычный цикл; перед `emit("done")` `cache.store(embedding, CachedAnswer(done.text, done.sources), domain)`;
     `done["cache_hit"] = False`; `done["cache_lookup_s"] = <float>`.
3. Статус-события не упорядочиваются иначе; `status: cache` — новый не-терминальный
   stage (конверт ADR-016 сохраняется).

## Сборка из env (runtime)

`build_semantic_cache() -> SemanticCache | None`:
- `SEMANTIC_CACHE_ENABLED` пусто/false → `None`;
- `SEMANTIC_CACHE_MODE`:
  - `inmemory` → `InMemorySemanticCache(threshold, ttl_s)` — единый потоко-safe
    инстанс (lock) для воркера;
  - `redis` → `RedisSemanticCache(url, threshold, ttl_s)`;
  - иначе → `ValueError`.
`build_pipeline(adapter_map, semantic_cache=None)` — проброс; `worker.main()`
собирает кэш один раз и передаёт в обе сборки (initial и hot-reload).

## Ограничения

- Инвалидация только TTL/ручная (`clear()`, `del query:sc:*`); COMMIT не чистит.
  Зафиксированное допущение: окно свежести — `≤ SEMANTIC_CACHE_TTL_S`; момент
  пересмотра — M4 (Eval), M5 (конфигуратор профилей), M6 (коннекторы).
- Сериализуемые ответы: `text` + `sources`; крупные ответы — вне скоупа прототипа.

### Не кэшируемые ответы (store() → no-op)

`store()` не вызывается для «плохих» ответов:
- пустой `text` (LLM вернул 0 дельт / обнулился);
- явный отказ (LLM написал «контекста недостаточно» — конкретный приём в
  `DEFAULT_SYSTEM_PROMPT`);
- сбой пайплайна (исключение → `done` не генерируется).

### Planned upgrade path (не реализуется в этом бандле)

При необходимости точной инвалидации при изменении данных — добавить
epoch-ревизию ключа: ingest после фактического COMMIT (`created_new=True`)
увеличивает `query:sc:rev:<domain>` в Valkey; кэш переключается на
`query:sc:<rev>:<domain>`, старые записи выпадают из поиска, TTL дочищает.
В бандле фиксируется как «planned», не реализуется; номер ADR уточнить
при apply (ADR-024 занят бандлом `architecture-atomic-commit`).

## Приёмочные критерии

- `test_semantic_cache.py`: hit/miss/порог/TTL/stats/clear; «плохие» ответы (пустой
  text, отказ, сбой) → store no-op, не возвращаются hit'ом; Redis — формат ключа,
  поля, JSON, HDEL (фейковый клиент, без реального Valkey).
- `test_retrieval_pipeline.py`: hit → LLM вызовов 0, ответ из кэша, `token` нет;
  miss → полный цикл + запись; повторный близкий → hit.
- `test_worker_hotreload.py`: кэш-объект сохраняется при пересборке.
- Полный `pytest`/`ruff`/`mypy` зелёно.