# Graduation Project

Автоматическая генерация и оценка качества тестовой документации с локальной
LLM. Проект самодостаточен: генератор, промпты, пресеты и нормализатор лежат
внутри папки, дополнительных ДЗ устанавливать не нужно. Из общего стенда
используются только `ohw-ollama` и образ `ohw-python:3.13` из `infra/`.

## Что делает

```text
input/*.md ──▶ generate ──▶ scenarios/*.md + generation.json
                   │
                   ▼
                 assess ──▶ quality-metrics.json
                   │
          ┌────────┴─────────┐
          ▼                  ▼
     visualize             report
   charts/*.png           report.md
```

- Генерирует 5 типов артефактов: `testcases`, `checklist`, `testplan`,
  `qareport`, `coveragematrix`.
- Считает 4 метрики: читаемость, чёткость целей, покрытие требований,
  консистентность, плюс взвешенный `overall`.
- Строит boxplot, radar и оценки по сценариям.
- Пишет JSON (источник правды) и Markdown-отчёт.

## Быстрый старт (Windows)

Нужны Docker Desktop и папка `infra/` рядом с проектом.

```powershell
cd graduation-project
.\run.ps1
```

Скрипт сам поднимет общий `ohw-ollama`, соберёт приложение и выполнит полный
пайплайн. Первый запуск скачивает модель и Python-зависимости, это может занять
несколько минут. Результаты — в `.\output`.

Linux/macOS:

```bash
cd graduation-project
./run.sh
```

## Быстрый старт (локально, без Docker)

Нужна запущенная Ollama на `localhost:11434` с моделью `qwen2.5:7b-instruct`.

```bash
uv sync --dev
uv run python -m src.workflow --input-dir input --output-dir output
```

Если Python не установлен, `uv` скачает переносимый CPython сам — это тот же
подход, что у портативных сборок, но без бинарей в репозитории:

```bash
uv python install 3.13
uv sync --frozen --no-dev
uv run --no-dev python -m src.workflow.main --input-dir input --output-dir output
```

## CLI

```text
python -m src.workflow [--generate] [--assess] [--visualize] [--api-tests] [--report]
    --input-dir input --output-dir output --type testcases
    --count 10 --runs 1 --temperature 0.7 --max-retries 3
    --url URL --normalize --preset ID [--preset ID ...]
    --prompt-dir DIR --base-url URL --model NAME --timeout SEC
    --api-base-url URL --api-contract FILE --api-cases 5
    --fail-under SCORE
```

Без флагов фаз выполняются все четыре. Фаза `api-tests` выполняется только при
явном `--api-tests`; пока адаптер не подключён, она возвращает код 2 с
объяснением (контракт — `openspec/specs/autotests/spec.md`). Фазы можно запускать отдельно по
сохранённым файлам:

```bash
python -m src.workflow --generate --count 5
python -m src.workflow --assess
python -m src.workflow --visualize
python -m src.workflow --report
```

`--runs 2` и больше даёт честную консистентность (Jaccard повторных генераций).
При `--runs 1` используется внутренняя эвристика, и отчёт это показывает.

## UI (Streamlit)

Второй фронт проекта — рабочее место на Streamlit. Оно вызывает те же стадии
`src/workflow/stages.py`, что и CLI, поэтому артефакты и метрики совпадают.

```bash
uv run python -m src.ui          # http://localhost:8501
docker compose up -d ui          # тот же адрес, общий стенд ohw_net
```

Пять вкладок: требования, генерация, метрики, отчёт и API-тесты. На вкладке
требований загружаются `.md`/`.txt`, текст можно править; перед генерацией файлы
копируются в `output/ui-input/`. Ollama опрашивается только по кнопке «Загрузить
модели», при старте сетевых запросов нет.

## Входные данные

Папка `input/` с файлами `.md` или `.txt`. Требования удобно нумеровать `BR-N`:

```markdown
## BR-1. Открытие формы
Пользователь открывает страницу регистрации. Форма должна содержать поля
«Email», «Пароль» и кнопку «Зарегистрироваться».
```

Поддерживается front-matter:

```markdown
---
type: checklist
---
```

Без front-matter тип задаётся флагом `--type`.

## Результаты

| Файл | Что внутри |
|---|---|
| `output/scenarios/<stem>.md` | сгенерированный артефакт |
| `output/generation.json` | снимок генерации для `--assess` |
| `output/quality-metrics.json` | метрики и реальные тест-кейсы |
| `output/report.md` | Markdown-отчёт |
| `output/charts/*.png` | графики |
| `output/api-tests.json` | результаты API-тестов (только с `--api-tests`) |

## Метрики

| Метрика | Формула | Вес |
|---|---|---:|
| Readability | `1 - |avg_words - 17| / 30` | 0.25 |
| Clarity of goals | 5 категорий × 0.2 | 0.25 |
| Scenario coverage | покрытые `BR-N` / все `BR-N` | 0.30 |
| Consistency | средний Jaccard повторных генераций | 0.20 |

`overall` — взвешенное среднее известных метрик. `n/a` означает «не измеримо»
(например, в требованиях нет `BR-N`), а не ноль. Подробности —
[`docs/metrics.md`](docs/metrics.md).

## Конфигурация

| Переменная | По умолчанию |
|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` |
| `OLLAMA_TIMEOUT` | `600` |
| `GRADUATION_PROMPT_DIR` | `src/generator/templates` |
| `GRADUATION_PRESETS_DIR` | `src/generator/presets` |
| `GRADUATION_NORMALIZER_CONFIG` | `src/generator/normalizer.toml` |

Скопируйте `.env.example` в `.env` для локального запуска.

## Общий стенд

```bash
./infra/up.sh graduation-project          # CPU
./infra/up.sh graduation-project --gpu    # NVIDIA
./infra/up.sh graduation-project --amd    # AMD ROCm
```

`compose.yaml` проекта содержит сервисы `app` (CLI) и `ui` (Streamlit),
подключает внешнюю сеть `ohw_net` и обращается к Ollama по
`http://ohw-ollama:11434`. Собственный Ollama не поднимается.

## Тесты и проверки

```bash
uv run pytest
uv run ruff check src scripts tests
uv run mypy src

# живой e2e на источнике из SDD проекта
RUN_OLLAMA_INTEGRATION=1 uv run pytest -m integration
```

Guard-тесты проверяют кодировку документации и контракт лаунчеров; они входят
в обычный прогон `uv run pytest`.

## Структура

```text
graduation-project/
├── src/
│   ├── generator/      # вендоренное ядро DZ2 + шаблоны и пресеты
│   ├── quality/        # 4 метрики
│   ├── visualization/  # графики
│   ├── reporting/      # JSON + Markdown
│   ├── autotests/      # шов API-тестов (без сети)
│   ├── ui/             # Streamlit-рабочее место
│   └── workflow/       # CLI, общие стадии и фазы
├── tests/              # unit + e2e (источник — SDD)
├── input/              # пример требований
├── openspec/           # спеки и изменения
├── docs/               # руководство, метрики, демо
├── SDD.md              # единый Software Design Document
├── Dockerfile
├── compose.yaml
├── infra.yaml
└── run.ps1 / run.sh / run.cmd
```

## Документация

- [`docs/user-guide.md`](docs/user-guide.md) — подробное руководство.
- [`docs/metrics.md`](docs/metrics.md) — формулы и ограничения метрик.
- [`docs/demo.md`](docs/demo.md) — пример живого прогона.
- [`SDD.md`](SDD.md) — архитектура и дизайн.
- [`openspec/`](openspec/) — требования и изменения.

## Возможное развитие

- **API-тесты.** Шов уже зафиксирован: порт `ApiTestRunner`, `api-tests.json` и
  раздел отчёта. Следующий шаг — адаптер ДЗ3 advanced (JSON-план + HTTP-движок)
  и целевой API. Пока сеть не используется. Контракт —
  [`openspec/specs/autotests/spec.md`](openspec/specs/autotests/spec.md).
- **ДЗ6.** Ревью автотестов не входит: он анализирует pytest-код, а advanced
  даёт JSON-план; вернёмся отдельным портом.
- **Портативный запуск.** Вместо вендоринга embedded Python используется `uv
  python install`; бинари в репозиторий не коммитятся.

## Ограничения

- UI не заменяет CLI: оба фронта работают поверх одних стадий.
- Нет аутентификации и многопользовательского режима UI.
- DZ6 (проверка/оптимизация автотестов) не входит.
- Модель не обучается, LLM-судья не используется как метрика.
- Результат требует ревью человеком.
