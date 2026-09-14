# Задачи

## 1. Спецификация

- [ ] 1.1. План и дизайн: `proposal.md` (готов), `design.md` — выбор размещения в
      `QueryPipeline.run` (после `embed`), структуры записей, TTL/порог.
- [ ] 1.2. `specs/add-semantic-cache/spec.md`: контракт `SemanticCache`, поведение
      hit/miss, поля `done`, env-переменные, Redis-ключи, ограничения.

## 2. Реализация

- [x] 2.1. `retrieval/semantic_cache.py`: `SemanticCache` (ABC) + `CachedAnswer`;
      `InMemorySemanticCache` (cos-порог, TTL, `stats()`, `clear()`).
- [x] 2.2. `RedisSemanticCache`: хэш `query:sc:<domain>`, поле `sc:<sha256[:12]>`,
      JSON `{embedding,text,sources,ts}`; lazy-`import redis`, TTL-чистка (HDEL при
      сканировании); дефолт `QUERY_REDIS_URL=redis://valkey:6379/0`.
- [x] 2.3. `QueryPipeline`: параметр `semantic_cache: SemanticCache | None = None`
      (None = выключено, поведение M2); miss → обычный цикл + `store()`;
      hit → статусы `embedding`→`cache{hit:true}`→`done` + `cache_hit`/`cache_lookup_s`,
      без обращений к store/LLM.
- [x] 2.4. `runtime.py`: `build_semantic_cache()` из env (`SEMANTIC_CACHE_ENABLED`,
      `SEMANTIC_CACHE_MODE`, `SEMANTIC_CACHE_THRESHOLD`, `SEMANTIC_CACHE_TTL_S`,
      `QUERY_REDIS_URL`); `build_pipeline(adapter_map, semantic_cache=...)`.
- [x] 2.5. `worker.py:main()`: собрать кэш один раз, передать в `build_pipeline`
      (и в initial, и в hot-reload пересборку — объект общий).
- [x] 2.6. `SemanticCache.store()`: no-op для «плохих» ответов — пустой `text`,
      явный отказ LLM («контекста недостаточно»), сбой пайплайна (решение по
      свежести, 2026-09-13).

## 3. Тесты

- [x] 3.1. `tests/test_semantic_cache.py`: hit ≥ порога, miss < порога,
      TTL-истечение (старые записи не отдаются/удаляются), `stats()`,
      пустой/нулевой вектор → miss; Redis-формат через фейковый клиент
      (ключ/поле/JSON/HDEL) без новых зависимостей.
- [x] 3.2. `tests/test_retrieval_pipeline.py`: hit → ответ равен закэшированному,
      LLM вызван 0 раз, нет `token`; miss → полный цикл и запись в кэш; повторный
      близкий запрос → hit.
- [x] 3.3. `tests/test_worker_hotreload.py`: при hot-reload кэш-объект сохраняется
      (одни и те же записи).
- [x] 3.4. `tests/test_semantic_cache.py`: «плохие» ответы не складируются
      (`store()` no-op) и не возвращаются hit'ом.
- [x] 3.5. `uv run pytest -q` зелёно (259 passed); `uv run ruff check .`, `uv run mypy src`
      чисто.

## 4. Документация и приёмка

- [x] 4.1. `docs/demo_runbook.md`: раздел «Семантический кэш (M3.3)» — включение,
      порог/TTL, сценарий hit/miss (SSE `cache_hit`), ручная очистка.
- [x] 4.2. Статус M3 (бандл 3/3) — таблица вех `docs/prototype_requirements.md`
      (строка M3 + чеклист «Веха 3-хвосты — Semantic Cache») + `docs/history.md`
      (Этап 11). Замечание: файла `protocol_requirements.md` в репо нет —
      фактический носитель статуса — `prototype_requirements.md`.
- [x] 4.3. Документ допущения TTL-инвалидации: ADR-025 (`docs/05_adr_log.md`,
      COMMIT кэш не чистит; planned upgrade path — epoch-bump
      `query:sc:<rev>:<domain>`; триггеры пересмотра — M4 Eval / M5 конфигуратор /
      M6 коннекторы) + упоминание в `docs/03_retriever.md` (§1, шаг 0).
      Номер ADR — 025 (ADR-024 занят бандлом A-2).
- [x] 4.4. Live-приёмка (mock-контур, `QUERY_QUEUE=redis`): стек config+graph+
      ingestion+llm поднят, `SEMANTIC_CACHE_ENABLED=true` — 1-й запрос
      `cache_hit:false` + токены, повторный близкий → `cache_hit:true` без token
      (`tests/test_e2e_semantic_cache.py`, PASSED). Backward-compat при unset —
      юнит-тест `test_no_cache_backward_compat` (поля `cache_hit` отсутствуют).
      compose.yaml: проброс `SEMANTIC_CACHE_*` в `query-worker` via env passthrough.