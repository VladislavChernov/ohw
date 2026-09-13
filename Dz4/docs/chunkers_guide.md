# Документация: Добавление чанкер-стратегий (Chunkers Guide)

> **Версия:** v1.0
> **Последнее обновление:** 2026-09-11
>
> См. также: `CONCEPT.md` §4.1 (CHUNK), §6.5 (namespace `chunking`), §2.6 (entry_points);
> `docs/02_pipeline_and_normalizer.md` §1 (этап CHUNK); `docs/04_services_config.md` §5
> (precedence настроек); прототип: `prototype/src/graphrag_proto/ingestion_service/pipeline/chunker.py`,
> `prototype/tests/test_chunker.py`.

---

## 1. Место чанкинга в пайплайне

- CHUNK — этап **№2 ядра** из 9 фиксированных (L3-01: пропуск этапа запрещён). Чанкинг нельзя вынести
  «наружу» как опциональную систему — это внутренняя стратегия ядра.
- Вход этапа — канонический документ (ADR-021). **Единица чанкинга — отдельный блок `text|code`**:
  блоки нарезаются ридером и обрабатываются независимо (чанк не пересекает границу блока).
- Стратегия выбирается per-job: `env > профиль домена > namespaces.yaml > дефолты M1 (512/64)`
  (`build_chunker_for(domain)`, см. `docs/04_services_config.md` §5).
- Выход этапа — упорядоченный список `ctx.chunks` + `chunks_meta` (`index`, `size_tokens`, далее
  `chunk_id`, `embedding`), которые потребляет EMBED и COMMIT.

```
блок (text|code) ──> chunker.chunk(text) ──> [chunk0, chunk1, …] ──> EMBED → COMMIT
                          ▲
              стратегия из namespace chunking (env/профиль/namespaces)
```

---

## 2. Контракт Chunker (обязательно для корректной работы)

| # | Требование | Почему (следствие нарушения) |
|---|------------|------------------------------|
| 1 | Класс — потомок `Chunker` (ABC), метод `chunk(text: str) -> list[str]`; пустая строка → `[]` | технический интерфейс; несоответствие — ошибка типизации/сборки |
| 2 | Вход — ОДИН блок, без знания об остальном документе | блоки обрабатываются независимо (ADR-021, L2-03) |
| 3 | Выход — стабильно упорядоченный список (детерминизм на одинаковом входе) | `chunk_id = sha256(source_url:index)` (`orchestrator.py:33`); недетерминизм ломает переиндексацию и связь осей L2-04 |
| 4 | Куски непустые (после `strip()`) | пустой кусок даёт пустой эмбеддинг на EMBED |
| 5 | Текст не теряется: куски покрывают вход блока | иначе тихая потеря контента (сообщения об этом нет) |
| 6 | Куски влезают в окно эмбеддера (`EMBEDDING_MAX_TOKENS`) | на этапе EMBED валидации размера нет — обрезка/потеря, ответственность стратегии |
| 7 | Внешние зависимости — ленивый импорт + fail-fast `RuntimeError` при построении | optional deps не установлены в базовом контуре; ошибка всплывает при выборе стратегии, а не посреди документа |
| 8 | Параметры — из конфига (env/namespaces/профиль), не хардкод | namespace `chunking` (`docs/04` §5) |
| 9 | Имя стратегии зарегистрировано в фабрике (см. §3 / §4) | иначе фабрика не резолвит имя → `ValueError` |

---

## 3. Добавление родной (in-core) стратегии — пошагово

1. Реализовать потомка `Chunker` в `pipeline/chunker.py` (рядом с существующими).
2. Добавить ветку в `_build_chunker` и имя — в `ALLOWED_CHUNKERS`.
3. Специфичные параметры — env-переменные по паттерну `INGEST_LANGCHAIN_SPLITTER` /
   `INGEST_LLAMAINDEX_PARSER` (чтение через `_env`/`_env_int`), размеры по умолчанию — M1 (512/64).
4. Внешние библиотеки: `[project.optional-dependencies] chunking` в `pyproject.toml`
   + `[[tool.mypy.overrides]]` для модулей.
5. Контрактные тесты (§5) в `tests/test_chunker.py`.
6. Обновить `docs/02` §1 (перечень стратегий), при необходимости `docs/04` §5 и этот гайд.

Эскиз:

```python
class MyChunker(Chunker):
    def __init__(self, chunk_size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP):
        self._sliding = SlidingWindowChunker(chunk_size, overlap)  # базовый примитив
    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        return self._sliding.chunk(text)  # заменить на свою логику

# _build_chunker:
#     if strategy == "my_chunker":
#         return MyChunker(chunk_size=chunk_size, overlap=overlap)
# ALLOWED_CHUNKERS = (..., "my_chunker")
```

---

## 4. Плагин через entry_points (реализовано, бандл add-chunker-entry-points)

> Механизм работает в прототипе: generic-реестр `graphrag_proto/plugin_registry.py`
> (discovery через `importlib.metadata`), группа `graphrag.chunkers`. Инфра-адаптеры
> остаются на жёстком `ADAPTER_CATALOG` — их переход на entry_points — отдельное решение.

- **Группа entry-point:** `graphrag.chunkers`. В pyproject пакета-плагина:

  ```toml
  [project.entry-points."graphrag.chunkers"]
  my_chunker = "my_pkg.chunker:build_my_chunker"
  ```

  где `build_my_chunker(*, chunk_size: int, overlap: int) -> Chunker` — фабрика **только
  с именованными** аргументами (не зависит от позиции полей настройки).
- **Discovery:** `plugin_registry.map_plugins(group)` — кэш на процесс; `clear_cache()`
  для тестов/горячей перезагрузки; загрузка фабрики — `load_plugin(group, name)`.
- **Резолюция имени** (в `build_chunker_for`/`build_chunker`): встроенные
  (`ALLOWED_CHUNKERS`) → плагин → `ValueError` с перечнем `list_chunkers()`
  (встроенные + имена плагинов).
- **Правила:**
  - встроенная стратегия имеет **приоритет**: плагин с тем же именем игнорируется
    (детерминированное поведение ядра);
  - сломанный импорт / entry point не callable / фабрика вернула не `Chunker` →
    `PluginLoadError` (`RuntimeError`) с именем плагина **без тихого fallback** на
    sliding_window (иначе маскируется ошибка конфигурации);
  - неизвестное имя — `ValueError` (как в M1), сообщение со списком доступных имён;
  - стратегия по-прежнему задаётся `chunking.strategy` — precedence не меняется (docs/04 §5).
- **Ops:** плагин ставится в run-образ ingestion (docker build / `uv sync`), фиксируется
  в инсталляционных справочниках (`docs/04`); отладочный хелпер `list_chunkers()`
  (встроенные + плагины) — для лога/валидации конфига.
- **Тесты:** `prototype/tests/test_chunker_plugins.py` (7): резолюция via env и профиль
  per-job, приоритет встроенных, fail-fast (импорт/тип), неизвестное имя со списком,
  `list_chunkers()`.

---

## 5. Контрактные тесты (чек-лист для новой стратегии)

- **Детерминизм:** два вызова на одинаковом входе → идентичный список.
- **Непустота:** нет `""` и whitespace-кусков; пустой вход → `[]`.
- **Покрытие:** ключевые фрагменты текста присутствуют в объединении кусков (для word-based —
  восстановление без потерь).
- **Граница окна:** ни один кусок не превышает `chunk_size` (для word/token-стратегий).
- **M1-совместимость:** при дефолтах поведение не хуже `sliding_window(512, 64)`.
- **Fail-fast:** внешняя зависимость без установки → `RuntimeError` при построении, не в `chunk()`.
- **Регистрация:** имя резолвится фабрикой; неизвестное имя → `ValueError`.

Эталоны — `prototype/tests/test_chunker.py` (20 тестов: M1-compat, валидация параметров,
structure-aware связки заголовков, 4 уровня precedence, fallback недоступного профиля, fail-fast
LangChain/LlamaIndex).