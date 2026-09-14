# Задачи

## 1. Спецификация

- [ ] 1.1. План и дизайн: `proposal.md` (готов), `design.md` — выбор размещения в
      `QueryPipeline.run` (после `embed`), структуры записей, TTL/порог.
- [ ] 1.2. `specs/add-semantic-cache/spec.md`: контракт `SemanticCache`, поведение
      hit/miss, поля `done`, env-переменные, Redis-ключи, ограничения.

## 2. Реализация

- [ ] 2.1. `retrieval/semantic_cache.py`: `SemanticCache` (ABC) + `CachedAnswer`;
      `InMemorySemanticCache` (cos-порог, TTL, `stats()`, `clear()`).
- [ ] 2.2. `RedisSemanticCache`: хэш `query:sc:<domain>`, поле `sc:<sha256[:12]>`,
      JSON `{embedding,text,sources,ts}`; lazy-`import redis`, TTL-чистка (HDEL при
      сканировании); дефолт `QUERY_REDIS_URL=redis://valkey:6379/0`.
- [ ] 2.3. `QueryPipeline`: параметр `semantic_cache: SemanticCache | None = None`
      (None = выключено, поведение M2); miss → обычный цикл + `store()`;
      hit → статусы `embedding`→`cache{hit:true}`→`done` + `cache_hit`/`cache_lookup_s`,
      без обращений к store/LLM.
- [ ] 2.4. `runtime.py`: `build_semantic_cache()` из env (`SEMANTIC_CACHE_ENABLED`,
      `SEMANTIC_CACHE_MODE`, `SEMANTIC_CACHE_THRESHOLD`, `SEMANTIC_CACHE_TTL_S`,
      `QUERY_REDIS_URL`); `build_pipeline(adapter_map, semantic_cache=...)`.
- [ ] 2.5. `worker.py:main()`: собрать кэш один раз, передать в `build_pipeline`
      (и в initial, и в hot-reload пересборку — объект общий).
- [ ] 2.6. `SemanticCache.store()`: no-op для «плохих» ответов — пустой `text`,
      явный отказ LLM («контекста недостаточно»), сбой пайплайна (решение по
      свежести, 2026-09-13).

## 3. Тесты

- [ ] 3.1. `tests/test_semantic_cache.py`: hit ≥ порога, miss < порога,
      TTL-истечение (старые записи не отдаются/удаляются), `stats()`,
      пустой/нулевой вектор → miss; Redis-формат через фейковый клиент
      (ключ/поле/JSON/HDEL) без новых зависимостей.
- [ ] 3.2. `tests/test_retrieval_pipeline.py`: hit → ответ равен закэшированному,
      LLM вызван 0 раз, нет `token`; miss → полный цикл и запись в кэш; повторный
      близкий запрос → hit.
- [ ] 3.3. `tests/test_worker_hotreload.py`: при hot-reload кэш-объект сохраняется
      (одни и те же записи).
- [ ] 3.4. `tests/test_semantic_cache.py`: «плохие» ответы не складируются
      (`store()` no-op) и не возвращаются hit'ом.
- [ ] 3.5. `uv run pytest -q` зелёно; `uv run ruff check .`, `uv run mypy src`
      чисто.

## 4. Документация и приёмка

- [ ] 4.1. `docs/demo_runbook.md`: раздел «Семантический кэш (M3.3)» — включение,
      порог/TTL, сценарий hit/miss (SSE `cache_hit`), ручная очистка.
- [ ] 4.2. `protocol_requirements.md` — статус M3 (бандл 3/3) + `docs/history.md`.
- [ ] 4.3. `docs/03_retriever.md`/ADR-запись: зафиксировать допущение TTL-инвалидации
      (COMMIT не чистит кэш), «planned upgrade path» (epoch-bump `query:sc:<rev>:<domain>`)
      и моменты пересмотра допущения (M4 Eval — кэш off в срезе; M5 конфигуратор
      профилей; M6 коннекторы). Номер ADR — уточнить при apply: ADR-024 занят бандлом
      `architecture-atomic-commit` (A-2).
- [ ] 4.4. Live-приёмка (mock-контур): `SEMANTIC_CACHE_ENABLED=true` — 1-й запрос
      `cache_hit:false`, повторный близкий → `cache_hit:true`, `token` отсутствует;
      при доступном Valkey — `QUERY_QUEUE=redis` тот же сценарий; чистый контур с
      `SEMANTIC_CACHE_ENABLED` unset — поведение идентично M2 (backward-compat).