# DZ3 Advanced — JSON Test-Plan Generator (no pytest)

> 👋 Человекочитаемый обзор, что это такое и зачем — в
> **[docs/overview.md](docs/overview.md)** (для QA/менеджеров). Ниже —
> техническая документация.

Расширенный вариант ДЗ3: LLM возвращает **не код**, а **JSON-план тестов**
(схема **v3**), который исполняет детерминированное **ядро** (только HTTP).
LLM-код никогда не запускается; pytest в рантайме не используется.

Простой вариант (`../simple`) — это «LLM пишет pytest-код → прогон». Advanced
меняет контракт: LLM проектирует план, ядро его исполняет.

## Архитектура

```
документация (страница API / contracts/base=XYZ / contracts/supplements=XXX)
        │  сборка промпта (плейсхолдеры)
        ▼
   LLM ──► JSON-план (steps/extract/cleanup/provisional/on_fail)  ──► валидатор
        │                                                              │ (покрытие,
        ▼                                                              │  cleanup у
   ЯДРО (только HTTP + ohw_kit.checks + отчёт)                         │  мутирующих,
        ▼                                                              │  переменные)
   отчёт (имя теста из плана, каждый шаг, каждая проверка, FAILURES)  ▲
                                                             feedback на уровень плана
```

Модули (`src/json_testgen_advanced/`):

| Модуль | Роль |
|---|---|
| `core.py` | HTTP-ядро: исполнение шагов, `{var}`-подстановка, `extract` (JSONPath), `on_fail`, `cleanup`, оценка `expect` через **`ohw_kit.checks.evaluate`**, параллельное исполнение сценариев с изоляцией мутируемых коллекций |
| `plan.py` | Модель плана, парсинг, нормализация плоской формы v1 → steps, валидатор (покрытие, mutating→cleanup/provisional, переменные), схема `plan_schema/v3.json` |
| `docs.py` | Слои документации: детектор источника (OpenAPI/JSON/YAML, Markdown, URL), `Context`/`base`/`supplements`, OpenAPI-digest + маркеры, мердж, 4 комбинации |
| `generator.py` | Feedback-луп на уровне плана через **`ohw_kit.ollama_client.OllamaClient`** (`json_mode`) |
| `report.py` | Человекочитаемый markdown-отчёт (без pytest) через `ohw_kit.render.render_markdown` |
| `prompt.py` | Чтение шаблона `input/prompt.txt` |
| `config.py` / `cli.py` | Конфиг из env + CLI-пайплайн |
| `openapi_reader.py` | Проектные ридеры `.json/.yaml/.yml`, регистрируемые в `ohw_kit.io` |

## Переиспользование kit (правило «только через kit, без копирования»)

Advanced зависит от `../kit` (`uv add ../kit`) и использует:
- `ohw_kit.ollama_client.OllamaClient` — весь LLM-доступ (`json_mode=True`,
  `httpx.MockTransport` для тестов). Своего ollama-модуля нет.
- `ohw_kit.checks.evaluate` — эталонный JSONPath-оценщик (свой не пишем).
- `ohw_kit.jsonreply.extract_json` + `ValidationResult` — парсинг JSON и фидбек.
- `ohw_kit.io.load_input` + `register_reader` — чтение контрактов.
- `ohw_kit.render.render_markdown` — обёртка markdown-отчёта.

Домашне-специфичное (ядро, валидатор плана, слои доки) — в проекте.

## JSON-контракт v3 (что отдаёт модель)

```
{ "service": ..., "tests": [ { "name", "description?",
    "vars": {...},            // стартовые переменные
    "steps": [ { "name"?,
      "request": {"method","path","headers"?,"body"?},
      "extract": {"var": "JSONPath"},   // сохранить из ответа
      "expect": {"status_code"?, "checks"?:[{"op","path","value"}]},
      "on_fail": "abort"|"continue" } ],
    "cleanup": [ ...шаги.. ],            // выполняется всегда после steps,
                                           // не влияет на статус
    "provisional": true } ] }            // create без cleanup
```

Допустимые `op` в `checks`: `eq, len_eq, contains, fields_eq, type`.
JSONPath-подмножество: `$`, `.key`, `[i]`, `[*]`, `..key`.

Плоская форма v1 (`request`/`expect` прямо на тесте) принимается как
сокращение для одношаговых READ-сценариев и нормализуется к `steps`.

Валидатор: (а) покрытие ресурсов×глаголов; (б) мутирующий сценарий обязан
иметь `cleanup` либо `provisional` у create; (в) переменные `{var}` в cleanup
должны быть заданы в `vars` или извлечены через `extract` в steps; (г) шаг с
неразрешённым `{var}` (нет такого ключа в `vars`/`extract`) падает сразу с
явной ошибкой — литералы вида `/albums/{id}` в API не отправляются.

## Параллельное исполнение сценариев

Сценарии плана выполняются **параллельно** (по умолчанию до 8 потоков,
`execute_plan(..., max_workers=N)`), при этом изоляция данных гарантирована
двумя уровнями:

1. **Шаги внутри одного сценария — строго последовательно**: мутирующий шаг и
   его `cleanup` никогда не чередуются; `{var}`-подстановка и `extract` идут
   в порядке плана.

2. **Сценарии, мутирующие одну коллекцию, сериализуются.** Перед запуском
   ядро статически вычисляет для каждого сценария набор «мутируемых
   коллекций»: метод `PUT/PATCH/DELETE` + префикс пути до первого
   плейсхолдера (`/posts/{id}` → `/posts`). Сценарии с общей коллекцией
   объединяются в группу (с транзитивным слиянием) и внутри группы идут
   последовательно. Группы и read-only сценарии выполняются параллельно.

3. **`POST` не считается конфликтом**: сервер выдаёт свежий `id`, параллельные
   создания не сталкиваются. Поэтому предпочтительный паттерн изоляции —
   сценарий создаёт свою сущность (`POST` + `extract` id) и мутирует только её.

Статический анализ консервативен: он сравнивает **префиксы коллекций**, а не
конкретные `id` (два сценария с `PUT /posts/99001` и `PUT /posts/99002`
пойдут последовательно, хотя сущности разные). Гарантия отсутствия гонок
важнее доли лишней сериализации.

Требование к модели зашито в промпт (данные сценария не пересекаются:
собственная сущность через `POST`+`extract` либо уникальный фиксированный
`id`), но ядро не полагается на обещание модели — группировка вычисляется
детерминированно по самому плану.

Порядок сценариев **в отчёте всегда соответствует порядку плана**, независимо
от реального порядка завершения потоков.

## Контракт документации (4 сценария)

| Случай | Сборка промпта | Режим (`doc_mode`) |
|---|---|---|
| API + базовая спека (XYZ) | Context + Контракт | `api+xyz` |
| API + дополнения (XXX, без XYZ) | Context + Дополнение («базовая спека недоступна») | `api+xxx` |
| API + XYZ + XXX | Context + мердж XYZ+XXX (XXX побеждает, конфликты помечаются) | `api+xyz+xxx` |
| Только API | только Context; best effort | `api-only` / `none` |

Каталог контрактов (`input/contracts/`): `base/` — OpenAPI-спека,
`supplements/` — дополнения, `page/api.md` — описание страницы (контекст).

## Запуск

Три способа: devcontainer (основной), локально с системным Python, Docker —
подробно описаны в корневом [`../README.md` → «Способы запуска»](../README.md#способы-запуска).
Кратко, локально:

```bash
# 1) поднять общий ollama (из каталога ohw/infra; для GPU добавь --gpu)
cd ../infra && ./up.sh ../dz3 && cd advanced
uv sync --dev
uv run json-testgen-advanced

# либо целиком в Docker (E2E):
cd ../.. && docker compose -f dz3/compose.yaml up --build
```

```powershell
cd ohw/dz3/advanced
uv sync --dev

# положить в input/contracts/ base-спеку и дополнения, шаблон уже в input/prompt.txt
uv run json-testgen-advanced
```

CLI-флаги:

```
--service URL                 базовый URL целевого API (по умолч. jsonplaceholder)
--input-dir DIR               каталог с шаблоном промпта (по умолчанию ./input)
--contracts-dir DIR           каталог контрактов (по умолчанию ./input/contracts)
--output-dir DIR              каталог отчёта (по умолчанию ./output)
--prompt-file FILE            конкретный шаблон
--max-retries N               retry-бюджет feedback-лупа (по умолч. 3)
--temperature F               температура семплирования
--required-resources LIST     обязательные ресурсы (env REQUIRED_RESOURCES)
--no-run                      сгенерировать план, но не исполнять
--save-prompt                 сохранить заполненный промпт в output/
```

Результат: `output/plan.json` (план от модели), `output/report.md` (отчёт ядра).

## Чем отличается от simple

| | simple | advanced |
|---|---|---|
| LLM выдаёт | pytest-код | JSON-план (данные) |
| Исполняется код LLM? | да | нет (детерминированное ядро) |
| pytest в рантайме | да | нет |
| Свой ollama-клиент | да (история) | нет — только `ohw_kit` |
| Отчёт | pytest + humanized Failed tests | ядро: имя из плана, шаг+проверка, FAILURES |

## Разработка

```powershell
uv run pytest -q      # 50 тестов (MockTransport, без сети)
uv run ruff check src tests
uv run mypy src
```
