# Задачи

## 1. Generic-реестр entry_points

- [x] 1.1. `prototype/src/graphrag_proto/plugin_registry.py`: `map_plugins(group)`
      (`importlib.metadata.entry_points(group=...)`, `lru_cache`), `load_plugin(group, name)`,
      `list_plugins(group)`, `clear_cache()`; ошибки `PluginMissingError`/`PluginLoadError`
      (наследники `RuntimeError`).
- [x] 1.2. `load_plugin` возвращает callable фабрику; не-найден → `PluginMissingError`,
      сбой импорта / не callable → `PluginLoadError`.

## 2. Интеграция в чанкеры

- [x] 2.1. `chunker.py`: константа `CHUNKER_ENTRY_POINT_GROUP = "graphrag.chunkers"`;
      `list_chunkers()` = `ALLOWED_CHUNKERS` + имена плагинов (без конфликтующих).
- [x] 2.2. `_build_chunker`: для имени вне `ALLOWED_CHUNKERS` — поиск плагина; результат
      проверяется `isinstance(Chunker)`; сбой — `PluginLoadError` с именем плагина,
      БЕЗ fallback; итоговое `ValueError` для неизвестного имени — со списком `list_chunkers()`.
- [x] 2.3. Встроенные стратегии имеют приоритет над плагином с тем же именем (ветка built-in
      раньше discovery).

## 3. Тесты

- [x] 3.1. `tests/test_chunker_plugins.py`: резолюция плагина через env `INGEST_CHUNKER`
      (fake entry point: модуль в `sys.modules` + monkeypatch `plugin_registry.map_plugins`).
- [x] 3.2. Приоритет встроенных: плагин с именем `sliding_window` не перекрывает builtin.
- [x] 3.3. Fail-fast: сломанный импорт плагина → `RuntimeError`; фабрика вернула не `Chunker` →
      `RuntimeError`.
- [x] 3.4. Неизвестное имя → `ValueError`, сообщение содержит имена плагинов.
- [x] 3.5. `list_chunkers()` содержит встроенные + плагины (без конфликтующих).

## 4. Документация

- [x] 4.1. `docs/chunkers_guide.md` §4: статус «реализован», описание API
      (`plugin_registry`, `list_chunkers`, фабрика `(*, chunk_size, overlap)`, приоритеты, ошибки).
- [x] 4.2. `CONCEPT.md` §4.1: упоминание entry_points для сторонних стратегий.
- [x] 4.3. `docs/prototype_requirements.md` Веха 3-хвосты: задача «Entry-point плагины» `[x]`.
- [x] 4.4. `docs/history.md`: запись бандла (в разделе M3).

## 5. Полная верификация

- [x] 5.1. `uv run pytest -q` (все зелёные), `uv run ruff check src/ tests/`,
      `uv run mypy src/` — чисто.