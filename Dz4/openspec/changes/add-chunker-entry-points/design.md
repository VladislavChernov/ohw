# Design: add-chunker-entry-points

## Контекст

- Чанкер выбирается per-job: `build_chunker_for(domain)` → `_strategy_values` → `_build_chunker`.
  Фабрика `_build_chunker` сегодня жёсткая: ветки по `ALLOWED_CHUNKERS`, неизвестное имя →
  `ValueError("strategy=...: допустимо ...")`.
- Entry-point контракт (guide §4): группа `graphrag.chunkers`, запись
  `имя = "модуль:фабрика"`, фабрика `(*, chunk_size, overlap) -> Chunker`.
- Инфра-адаптеры остаются на `ADAPTER_CATALOG`; этот бандл не переводит их.

## Ключевые решения

1. **Общий примитив discovery — `plugin_registry.py` (пакет `graphrag_proto/`).**
   - `map_plugins(group) -> dict[str, EntryPoint]` — с `functools.lru_cache` (один скан
     дистрибутивов на процесс; для горячей подгрузки — `clear_cache()`).
   - `load_plugin(group, name) -> Callable[..., Any]` — импортирует entry point;
     не найден → `PluginMissingError`; сбой импорта/не callable → `PluginLoadError`.
   - `list_plugins(group) -> tuple[str, ...]` — имена (без загрузки модулей).
   - Исключения наследуют `RuntimeError` (fail-fast, без тихого fallback).
   - Примитив не знает про «чанкеры» — переиспользуем для будущих групп адаптеров.
2. **Приоритет встроенных стратегий.** Ветка built-in в `_build_chunker` проверяется
   раньше обнаружения плагинов; плагин с именем встроенной стратегии игнорируется
   (детерминированный приоритет ядра, задокументирован в guide).
3. **Фабрика плагина.** Сигнатура `factory(*, chunk_size: int, overlap: int) -> Chunker`
   (только именованные аргументы — не зависит от позиции в `_strategy_values`).
   Ожидается `isinstance(result, Chunker)`, иначе `PluginLoadError`.
4. **Классификация ошибок.**
   - неизвестное имя (ни встроенное, ни плагин) → `ValueError` со списком `list_chunkers()`
     (совместимо с `test_chunker.py::test_build_chunker_invalid_strategy_raises`);
   - имя плагина есть, но импорт сломан / не фабрика / не `Chunker` → `PluginLoadError`;
   - fallback на sliding_window при сбое плагина НЕ допустим (маскировка ошибки конфигурации).
5. **Точка поиска плагинов.** Discovery вызывается только для имён вне `ALLOWED_CHUNKERS` —
   на пути по умолчанию (sliding_window и др.) плагины не сканируются.
6. **Тестирование без установки пакетов.** fake entry point: реальный модуль в `sys.modules`
   + `monkeypatch` на `graphrag_proto.plugin_registry.map_plugins` (паттерн строкового
   monkeypatch, как `_fetch_profile` в `test_chunker.py`).

## Модули

```
prototype/src/graphrag_proto/plugin_registry.py            # новый: generic discovery
prototype/src/graphrag_proto/ingestion_service/pipeline/chunker.py
                                                           # правки: группа, ветка плагина,
                                                           # list_chunkers()
prototype/tests/test_chunker_plugins.py                    # новый: 6+ тестов
```

## Не-регрессия

- `test_chunker.py` (20 тестов) и поведение M1 — без изменений: дефолты sliding 512/64,
  precedence, структура ошибок (`ValueError` для неизвестного имени).
- `ChunkStage`/`orchestrator.py` не трогаются (DI-резолвер `build_chunker_for` неизменен).
- `pyproject.toml` не меняется: группа декларируется пакетами-плагинами, ядру не нужно.
- pytest/ruff/mypy — зелёные.