# Proposal: Semantic Cache на Valkey (бандл 3/3 вехи M3)

## Почему

M3-план: после реальных эмбеддингов и реранкера веха завершается семантическим кэшем.
Повторные запросы (или близкие по смыслу) гоняют полный retrieval+LLM цикл, хотя
ответ уже считался. Кэш по косинусу эмбеддинга запроса возвращает готовый ответ
без обращения к store и LLM; эмбеддинг запроса уже считается контуром (ось L2-04),
инфраструктура Valkey в стеке есть (ADR-023 Task Queue).

## Что делаем

- Модуль `retrieval/semantic_cache.py`: интерфейс `SemanticCache` + две реализации:
  - `InMemorySemanticCache` — тесты/демо без Valkey (паттерн `InMemoryTaskQueue`);
  - `RedisSemanticCache` — хэш `query:sc:<domain>`, cos-порог, TTL, lazy-`redis`.
- Кэш-запись: `{text, sources}` от `done` + сопутствующий эмбеддинг запроса.
- Интеграция в `QueryPipeline.run` **после** `embed()`: miss → полный цикл + store;
  hit → SSE `status: embedding → status: cache{hit:true} → done` с закэшированными
  `text/sources` и `cache_hit: true`, **без** встреч с store/LLM (никаких `token`).
- `runtime.build_semantic_cache()` из env: `SEMANTIC_CACHE_ENABLED`,
  `SEMANTIC_CACHE_MODE` (inmemory|redis), `SEMANTIC_CACHE_THRESHOLD` (дефолт 0.85),
  `SEMANTIC_CACHE_TTL_S` (дефолт 3600), `QUERY_REDIS_URL`. Кэш **выключен по
  умолчанию** — backward-compat с M2/M3.
- Hot-reload (бандл 1/3) не теряет кэш: объект создаётся в `runtime.build_pipeline`
  вне зависимости от карты адаптеров, пересборка пайплайна переиспользует его.
- Качество записей: в `store()` не пишутся «плохие» ответы — пустой `text`, явный
  отказ LLM («контекста недостаточно» и т.п.), сбой пайплайна. Кэш не раздаёт мусор
  (решение на обсуждении свежести, 2026-09-13).
- Ограничение (зафиксированное допущение): COMMIT/переиндексация не инвалидирует
  кэш (нет catalog-revision в контуре запроса) — свежесть через TTL + ручной сброс;
  документируется в spec/runbook в стиле ADR-021.
- Путь развития (не реализуется в бандле, фиксируется как «planned upgrade path»):
  инвалидация по ревизии данных — epoch-bump ключа `query:sc:<rev>:<domain>` в Valkey
  при фактическом COMMIT (`created_new=True`); старые записи выпадают из поиска сами,
  TTL их дочищает. Моменты пересмотра допущения — M4 (Eval: детерминированный срез,
  кэш off), M5 (конфигуратор профилей: правка профиля меняет ответы), M6 (коннекторы:
  постоянно обновляемые данные).

## Спека

`specs/add-semantic-cache/spec.md` — контракт `SemanticCache`, поведение hit/miss,
поля `done` (`cache_hit`, `cache_lookup_s`, нулевые `generation/retrieval_time_s`),
env-переменные и дефолты, TTL/порог, ограничения.

## Проверка

1. Юнит: `tests/test_semantic_cache.py` — hit выше порога, miss ниже, TTL-истечение,
   `stats()`, формат Redis-ключа (через мок-клиент, без новых зависимостей).
2. Pipeline: `tests/test_retrieval_pipeline.py` — hit возвращает закэшированный ответ
   с `cache_hit:true` и **LLM не вызывается** (счётчик = 0); miss складывает ответ;
   повторный близкий запрос → hit.
3. Live mock-контур: `SEMANTIC_CACHE_ENABLED=true` — первый запрос `cache_hit:false`,
   повторный близкий → `cache_hit:true` и нет `token`; при `QUERY_QUEUE=redis` —
   тот же сценарий на Valkey (hash `query:sc:<domain>`).
4. Полный `pytest` + `ruff` + `mypy` чисто.