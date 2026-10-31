# Dz7 — генератор промптов для LLM

Генератор принимает требования и производит готовые промпты, по которым
локальная LLM (Ollama) строит пять видов тестовой документации:

| Тип | Что генерирует LLM | Формат ответа |
|---|---|---|
| `testcases` | тест-кейсы (позитивные/негативные) | JSON `{"cases": [...]}` |
| `checklist` | чек-лист по зонам | Markdown |
| `testplan` | тест-план с фиксированными разделами | Markdown |
| `qareport` | QA-отчёт о результатах | Markdown |
| `coveragematrix` | матрица покрытия требований | Markdown-таблица |

Связка с основным проектом: [Dz2](../dz2/) принимает требования из своей
входной директории (`../dz2/docs`), а Dz7 строит по ним промпты; затем Dz2
прогоняет полученные промпты через Ollama и сохраняет артефакты.

## Как это работает

```
requirements.md ──► dz7/generator.py ──► prompt.txt
                          │
                   templates/<type>.txt
                          │
                          ▼
              dz2 --prompt-dir <dir> ──► Ollama ──► output/<stem>.md
```

- Все тексты промптов лежат в `templates/<type>.txt`; в коде промптов нет.
- Служебные строки шаблона, начинающиеся с `#`, в готовый промпт не попадают.
- Плейсхолдеры: `{url}`, `{count}`, `{count_noun}`, `{positive}`,
  `{negative}`, `{requirement_rule}`, `{content}`, `{extra}`, `{testcases}`.
- `{extra}` заполняется выбранными пресетами (блок «Additional instructions»);
  без пресетов остаётся пустой строкой.
- `{testcases}` есть только в `coveragematrix`: это список реальных кейсов
  `- TC-01 (positive, BR-1): заголовок`. Пустой список превращается в явное
  «No test cases have been generated for these requirements yet.», и шаблон
  требует `—`/`Not Covered` вместо придуманных `TC-NN`.
- Подстановка не ломает литеральный JSON-пример `{"cases": [...]}`.
- Если URL неизвестен, строка `Target site: {url}` удаляется целиком.
- `{requirement_rule}` заполняется только когда в требованиях есть `BR-N`.

## Требования

- Python 3.11+ (используется `tomllib`); сторонних зависимостей нет.
- Требования берутся из входной директории основного проекта Dz2 —
  `../dz2/docs` (по умолчанию).
- Для сквозного прогона — Docker + Ollama (см. `../dz2/README.md`).

## Быстрый старт

Из каталога `dz7`:

```bash
# один промпт в stdout; требования ищутся в ../dz2/docs
python3 generator.py --requirements-file business-requirements_catalog-courses.md --type testcases

# требования строкой
python3 generator.py --requirements "BR-1: вход по email и паролю. \
BR-2: неверный пароль показывает ошибку." --url https://example.com --type testcases

# все пять промптов сразу
python3 generator.py --requirements-file business-requirements_catalog-courses.md \
    --all-types --output-dir output/prompts
```

Windows (PowerShell):

```powershell
python generator.py --requirements-file business-requirements_catalog-courses.md `
    --all-types --output-dir output\prompts
```

Демонстрационные требования лежат отдельно — в `dz2/examples/input`:

```powershell
python generator.py --input-dir ..\dz2\examples\input --requirements-file login.md `
    --all-types --output-dir output\prompts
```

## Пресеты (готовые примеры запросов)

Пресет — TOML-файл в `presets/`, который добавляет в промпт тематический блок
инструкций. Это не отдельный шаблон: базовый текст типа остаётся прежним,
меняется только `{extra}`.

| id | Что добавляет | Типы |
|---|---|---|
| `boundary-values` | пустой список, один элемент, максимум, нулевые счётчики, длинный ввод | testcases, checklist, coveragematrix |
| `negative-scenarios` | явные пути отказа с проверяемым результатом | testcases, checklist, qareport |
| `accessibility` | клавиатура, фокус, скринридер, контраст | testcases, checklist, qareport |
| `input-validation` | инъекции, длинные значения, параметры URL, работа без сессии | testcases, checklist, testplan |
| `regression` | что перепроверять после изменения, smoke-набор | testplan, qareport, checklist |
| `localization` | длинные переводы, форматы чисел и дат, язык сообщений | testcases, checklist |

```bash
# посмотреть список
python3 generator.py --list-presets

# промпт с двумя пресетами
python3 generator.py --requirements-file business-requirements_catalog-courses.md \
    --type testcases --preset boundary-values --preset localization

# все промпты: пресет применяется только к поддерживающим типам
python3 generator.py --requirements-file business-requirements_catalog-courses.md \
    --all-types --preset negative-scenarios --output-dir output/prompts
```

Правила:

- `id` обязан совпадать с именем файла; `types` по умолчанию — все пять типов.
- Выбор пресета для типа вне его `types` — ошибка (код 2), а не молчаливый
  пропуск: иначе результат выглядел бы полным, но без нужных проверок.
- Если в шаблоне нет `{extra}`, а пресет выбран — тоже ошибка.
- В `--all-types` один и тот же набор пресетов применяется к каждому типу
  отдельно.

## Передача промптов в Dz2

Вариант 1 (рекомендуется) — скопировать шаблоны и пресеты как есть: Dz2 сам
подставит требования каждого входного файла.

```bash
python3 generator.py --emit-templates --output-dir ../dz2/prompts
python3 generator.py --emit-presets --output-dir ../dz2/presets
```

Вариант 2 — сгенерировать готовый промпт под конкретный файл требований и
передать его каталог стенду. Подстановки в нём уже выполнены, поэтому один
такой промпт соответствует одному набору требований.

```bash
python3 generator.py --requirements-file business-requirements_catalog-courses.md \
    --all-types --output-dir output/rendered
cd ../dz2
uv run ai-testgen -n 5 --prompt-dir ../dz7/output/rendered
```

Затем в Dz2 (требования по умолчанию — `docs/`):

```bash
cd ../dz2
uv run ai-testgen -n 5
```

## Аргументы CLI

| Аргумент | По умолчанию | Описание |
|---|---|---|
| `--requirements TEXT` | — | требования строкой |
| `--requirements-file NAME` | — | путь или имя файла из `--input-dir` (взаимоисключимо с `--requirements`) |
| `--input-dir DIR` | `../dz2/docs` (или `paths.input_dir`) | входная директория требований основного проекта Dz2 |
| `--url` | из текста требований | URL тестируемого сайта |
| `--type` | `testcases` (или `generation.default_type`) | тип артефакта; алиас `testcase` = `testcases` |
| `--count` | `5` (или `generation.default_count`) | число тест-кейсов |
| `--all-types` | выкл. | сгенерировать все пять промптов |
| `--output FILE` | stdout | файл для промпта одного типа |
| `--output-dir DIR` | `output` (или `paths.output_dir`) | каталог для `--all-types` / `--emit-templates` |
| `--preset ID` | — | пресет из `presets/` (можно несколько) |
| `--list-presets` | — | показать доступные пресеты и выйти |
| `--presets-dir DIR` | `presets` | альтернативный каталог пресетов |
| `--emit-templates` | выкл. | скопировать шаблоны без изменений |
| `--emit-presets` | выкл. | скопировать пресеты (`*.toml`) без изменений |
| `--templates-dir DIR` | `templates` | альтернативный набор шаблонов |
| `--config PATH` | `./config.toml` | TOML-конфиг |

Коды выхода: `0` — успех, `2` — ошибка аргументов, отсутствующий шаблон
или пресет, пресет для неподходящего типа, неизвестный тип или плейсхолдер,
нечитаемый файл требований.

## Конфигурация

```toml
[generation]
default_count = 5
default_type = "testcases"

[paths]
templates_dir = "templates"   # относительно файла конфига
presets_dir = "presets"       # относительно файла конфига
input_dir = "../dz2/docs"     # относительно файла конфига
output_dir = "output"         # относительно текущего каталога
```

CLI-аргументы приоритетнее конфига; отсутствие `config.toml` ошибкой не
считается.

## Тесты

```bash
cd dz7
python3 -m unittest discover -s tests -v
```

Покрыто: подстановка всех плейсхолдеров, отсутствие «протёкших» `{...}`,
валидность JSON-примера, правило `BR-N`, удаление строки URL, ошибки
неизвестного типа/шаблона, `--emit-templates`, `--emit-presets`, `--all-types`,
пресеты (загрузка, применимость по типам, `{extra}`), чтение требований из
файла и конфига.

`--emit-templates` и `--emit-presets` копируют файлы байт-в-байт: Dz2 читает
их без правок, а тест `dz2/tests/test_prompts.py` сверяет каталоги.

## Запуск стенда

Промпты исполняет Dz2: он читает шаблоны из `dz2/prompts`, пресеты из
`dz2/presets` и ходит в общий `ohw-ollama`. Скрипты стенда лежат в
`dz2/scripts`; они сами проверяют Docker (и NVIDIA для GPU), собирают образ и
ждут health. Требования по умолчанию — `dz2/docs/`.

| Платформа | Режим | Команда |
|---|---|---|
| Windows | NVIDIA GPU | `.\scripts\up-gpu.ps1` |
| Windows | CPU | `.\scripts\up-cpu.ps1` |
| Linux | NVIDIA GPU | `./scripts/up-gpu.sh` |
| Linux | CPU | `./scripts/up-cpu.sh` |
| macOS | CPU | `./scripts/up-cpu.sh` (NVIDIA в контейнер не пробрасывается) |

Перед запуском перейдите в `dz2`:

```powershell
cd ../dz2
.\scripts\up-gpu.ps1          # Windows + NVIDIA
.\scripts\up-cpu.ps1          # Windows CPU
```

```bash
cd ../dz2
./scripts/up-gpu.sh           # Linux + NVIDIA
./scripts/up-cpu.sh           # Linux / macOS CPU
```

После старта: UI — `http://localhost:8501`, Ollama — `http://localhost:11434`.
Остановка: `.\scripts\down.ps1` (Windows) или `./scripts/down.sh` (Linux/macOS);
флаг `-Infra`/`-i` дополнительно останавливает общий `ohw-ollama`.
На CPU qwen2.5:7b генерирует заметно медленнее, поэтому таймаут запроса в UI
или `timeout` в `[ollama]` стоит поднять до 600–900 с.

## Рабочее место (Streamlit)

Промпт-генератор удобнее показывать в интерфейсе: видно, из какого шаблона и
каких пресетов собран запрос, что ответила модель и как правится артефакт.
Интерфейс живёт в основном проекте — `dz2/src/ai_testgen/ui` — и вызывает то
же ядро, что CLI, поэтому промпты и валидация не расходятся.

```bash
cd ../dz2
uv sync --dev
uv run ai-testgen-ui        # http://localhost:8501
# или в Docker: .\scripts\up-gpu.ps1 / .\scripts\up-cpu.ps1 (Windows),
# ./scripts/up-gpu.sh / ./scripts/up-cpu.sh (Linux/macOS)
```

Что важно для ДЗ7:

- на вкладке «Требования» файлы (`.md`/`.txt`) загружаются через file uploader,
  как в `QA_RAG_Studio`, — каталог указывать не нужно;
- вкладка «Промпт» показывает результат подстановки шаблона и выбранных
  пресетов; текст можно поправить перед отправкой;
- вкладка «Оценка» просит LLM оценить текст промпта по четырём критериям и
  сохраняет баллы, комментарии и `prompt_sha256` в
  `output/quality/<type>[-<preset>]-<run-id>.json` (UTC-метка прогона в имени,
  ссылка на сгенерированный артефакт с sha256 внутри) — из них собирается
  таблица в [`docs/prompt-quality.md`](docs/prompt-quality.md); ручная правка
  оценки возможна, но исходная оценка модельная.

Подробно — [`docs/workbench.md`](docs/workbench.md).

Почему промпт собирается детерминированно и как на нём оценивать деградацию
модели — [`prompt-determinism.md`](prompt-determinism.md).

## Оценка качества промптов

Методика и результаты прогона через Dz2 + Ollama — в
[`docs/prompt-quality.md`](docs/prompt-quality.md). Критерии: читаемость,
контекстность, чёткость целей, покрытие сценариев.

## Статус

- Реализовано: шаблонный движок, CLI, конфиг, `--emit-templates`,
  `--emit-presets`, пресеты и `{extra}`, тесты, пять типов, рабочее место
  Streamlit, инструкции запуска.
- Проверено: сквозной прогон на Docker + общий Ollama (10 промптов), таблица
  качества заполнена из JSON (`docs/prompt-quality.md`), прогон пяти типов
  через рабочее место автоматизирован тестом (`test_ui_app.py`).
- Ограничение: Linux/macOS-скрипты стенда проверены синтаксически; на Windows
  end-to-end прогон выполнен для CPU и GPU.
