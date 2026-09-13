# Proposal: Entry-point плагины чанкер-стратегий (graphrag.chunkers)

## Почему

`CONCEPT.md` §2.6 декларирует подключение сторонних реализаций через entry_points
(Python setuptools) без правки ядра, но механизм не реализован ни для адаптеров, ни для
чанкеров: реестры жёсткие (`ALLOWED_CHUNKERS` в `chunker.py`, `ADAPTER_CATALOG` у адаптеров).
Чтобы добавить новую стратегию чанкинга сегодня, нужно править код ядра
(`_build_chunker` + `ALLOWED_CHUNKERS`) — это противоречит духу §2.6 и создаёт vendor lock-in.

`docs/chunkers_guide.md` §4 уже зафиксировал целевой контракт (группа `graphrag.chunkers`,
фабрика `-> Chunker`, fail-fast). Бандл реализует этот механизм для чанкеров
и кладёт generic-примитив discovery, пригодный для будущих групп адаптеров.

## Что делаем

- **Generic-реестр `graphrag_proto/plugin_registry.py`**: discovery группы entry_points
  (`importlib.metadata.entry_points(group=...)`) с кэшем на процесс, `load_plugin(group, name)`,
  `list_plugins(group)`, `clear_cache()`; типизированные ошибки `PluginMissingError` /
  `PluginLoadError`.
- **Интеграция в `pipeline/chunker.py`**: группа `graphrag.chunkers`; ветка «неизвестное имя →
  плагин» в `build_chunker`/`build_chunker_for` (через общий `_build_chunker`); встроенные
  стратегии имеют приоритет; сообщение об ошибке использует `list_chunkers()`
  (встроенные + плагины).
- **Правила корректности:** результат фабрики проверяется `isinstance(Chunker)`;
  сломанный импорт/не фабрика/не тот тип — `PluginLoadError(RuntimeError)` с именем плагина,
  **без тихого fallback** на sliding_window; неизвестное имя — `ValueError` (как в M1,
  теперь — со списком доступного).
- **Тесты** `tests/test_chunker_plugins.py` с fake entry-point: резолюция плагина,
  приоритет встроенных, fail-fast при сломанном импорте, неверный тип результата,
  неизвестное имя со списком плагинов, вкл. через env + профиль.
- **Документация:** `docs/chunkers_guide.md` §4 статус «проект» → «реализовано»;
  `CONCEPT.md` §4.1 (упоминание entry_points); `docs/prototype_requirements.md`
  (задача M3-хвостов); `docs/history.md` (запись бандла).

## Границы (что НЕ входит)

- Слоты инфра-адаптеров (`graph_store`, `vector_store`, `llm`, `embeddings`, `reranker`)
  НЕ переводятся на entry_points в этом бандле — generic-примитив готов для них,
  интеграция — отдельным решением (в т.ч. возможное расширение L1-02).
- `pipeline/chunker.py` не реструктурируется в пакет `chunkers/` — реестр выносится
  отдельно, интеграция точечная.

## Проверка

1. `tests/test_chunker_plugins.py` (6+ тестов) зелёные; полный `uv run pytest -q` — без регрессий.
2. `uv run ruff check src/ tests/`, `uv run mypy src/` — чисто.
3. Ручная проверка: пакет с entry-point `graphrag.chunkers` → `INGEST_CHUNKER=<имя>`
   резолвит плагин; неизвестное имя — `ValueError` со списком; сломанный импорт — `RuntimeError`.
4. `ALLOWED_CHUNKERS` + существующие 20 тестов `test_chunker.py` — поведение M1 не изменилось.