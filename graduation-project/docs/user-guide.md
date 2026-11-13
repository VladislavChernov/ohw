# Руководство пользователя

## 1. Что нужно

- Docker Desktop (рекомендуется) **или** Python 3.11+ и `uv`.
- Общий стенд `infra/` рядом с проектом — для Docker-запуска.
- Модель `qwen2.5:7b-instruct` в `ohw-ollama` (скачивается автоматически при
  первом старте общего стенда).

Проверить, что Ollama доступна:

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
```

## 2. Запуск

### Docker (общий стенд)

```powershell
.\run.ps1
```

Скрипт:

1. проверяет сеть `ohw_net`;
2. при необходимости выполняет
   `docker compose -f ../infra/compose.yaml --profile ollama up -d`;
3. собирает образ приложения из `ohw-python:3.13`;
4. запускает `app` с фазами `generate → assess → visualize → report`.

Linux/macOS — `./run.sh`. Для cmd — `run.cmd`.

Передать свои аргументы CLI:

```powershell
.\run.ps1 --count 5 --runs 2 --normalize
```

### Локально

```bash
uv sync --dev
uv run python -m src.workflow --input-dir input --output-dir output
```

Если Ollama на другом адресе:

```bash
OLLAMA_BASE_URL=http://localhost:11434 uv run python -m src.workflow
```

#### Портативный запуск

Если Python не установлен, `uv` скачает переносимый CPython. Это замена
портативной сборке с embedded Python — без бинарей в репозитории:

```bash
uv python install 3.13
uv sync --frozen --no-dev
uv run --no-dev python -m src.workflow.main --input-dir input --output-dir output
```

Модель и Ollama всё равно остаются внешними: их нельзя встроить в папку
проекта.

### UI (Streamlit)

То же ядро, но в браузере:

```bash
uv run python -m src.ui
```

Или в Docker, вместе с общим стендом:

```powershell
docker compose up -d ui
```

Адрес — <http://localhost:8501>. Порядок работы:

1. В боковой панели проверьте URL Ollama и нажмите «Загрузить модели» (без
   нажатия сетевых запросов нет).
2. На вкладке «Требования» загрузите `.md`/`.txt`, при необходимости
   отредактируйте текст и выберите тип, число кейсов, повторы и пресеты.
3. На вкладке «Промпт» посмотрите, как шаблон DZ7 и пресеты собирают запрос;
   при необходимости отредактируйте промпт (правка применяется к выбранному
   файлу).
4. Нажмите «Сгенерировать» на вкладке «Генерация». Файлы копируются в
   `output/ui-input/`, результаты — в `output/scenarios/`. Чекбокс «Продолжать
   при ошибке Ollama» соответствует CLI-флагу `--continue-on-error`.
5. На вкладке «Метрики» пересчитайте оценки и постройте графики.
6. На вкладке «Отчёт» сформируйте `report.md` и скачайте артефакты.

CLI и UI можно запускать по очереди: они читают и пишут одни и те же файлы в
`output/`.

## 3. Входные требования

Положите файлы `.md` или `.txt` в `input/` (или укажите `--input-dir`).

Рекомендуемый формат — разделы с `BR-N`:

```markdown
## BR-1. Вход в систему
Пользователь вводит email и пароль. При верных данных должен открываться
личный кабинет.

## BR-2. Неверный пароль
При неверном пароле должно отображаться сообщение «Неверный пароль».
```

Если `BR-N` нет, генерация работает, но покрытие требований будет `n/a`.

URL можно указать в тексте (`https://...`) или флагом `--url`.

### Front-matter

```markdown
---
type: testplan
---
```

Допустимые значения: `testcases`, `checklist`, `testplan`, `qareport`,
`coveragematrix`. Без front-matter используется `--type` (по умолчанию
`testcases`).

## 4. Фазы

| Фаза | Что делает | Результат |
|---|---|---|
| `generate` | промпт → Ollama → валидация → retry → рендер | `scenarios/*.md`, `generation.json` |
| `assess` | пересчёт 4 метрик по снимку | `quality-metrics.json` |
| `visualize` | графики | `charts/*.png` |
| `report` | Markdown-отчёт | `report.md` |

Без флагов выполняются все. Примеры:

```bash
# только сгенерировать
python -m src.workflow --generate --count 5

# пересчитать метрики после ручной правки rendered-текста в generation.json
python -m src.workflow --assess

# перестроить графики и отчёт
python -m src.workflow --visualize --report
```

Важно: `assess` читает `generation.json`, а `visualize`/`report` —
`quality-metrics.json`. Если файла нет, CLI вернёт код 2 и подскажет, какую фазу
запустить.

## 5. Параметры

| Флаг | Смысл |
|---|---|
| `--input-dir` | папка требований, по умолчанию `input` |
| `--output-dir` | папка результатов, по умолчанию `output` |
| `--type` | тип артефакта |
| `--count` | кейсов на файл |
| `--runs` | повторов на сценарий; ≥ 2 для честной консистентности |
| `--temperature` | температура модели |
| `--max-retries` | повторы при невалидном ответе |
| `--url` | URL тестируемого сайта |
| `--normalize` | нормализовать требования |
| `--preset` | id пресета, можно несколько раз |
| `--prompt-dir` | свои шаблоны промптов |
| `--base-url` | адрес Ollama |
| `--model` | имя модели |
| `--timeout` | таймаут запроса, секунды |
| `--continue-on-error` | продолжать при недоступности Ollama для отдельного файла |
| `--quality-weights` | веса `overall`, см. ниже |
| `--fail-under` | порог `overall` для кода 5 |

### Веса overall

По умолчанию: readability `0.25`, clarity_goals `0.25`, scenario_coverage
`0.30`, consistency `0.20`. Переопределение:

```bash
python -m src.workflow --quality-weights \
  readability=0.20,clarity_goals=0.20,scenario_coverage=0.40,consistency=0.20
```

Или через `GRADUATION_QUALITY_WEIGHTS` в том же формате. Флаг важнее env.
Нужны ровно четыре известные метрики, неотрицательные числа и сумма `1.0`;
ошибка даёт код 2. Веса сохраняются в `generation.json` и
`quality-metrics.json` и печатаются в отчёте, поэтому `--assess` пересчитывает
метрики с теми же весами. Файлы без `quality_weights` используют значения по
умолчанию.

### Ошибки Ollama

Без `--continue-on-error` ошибка соединения с Ollama прерывает генерацию, и CLI
возвращает код 3. С флагом ошибка записывается для текущего файла в `errors`,
остальные файлы обрабатываются; успешные сценарии доступны фазам `assess`,
`visualize` и `report`. Если не сгенерирован ни один сценарий, код всё равно 3.

### Пресеты

Список доступных:

```bash
ls src/generator/presets
```

Использование:

```bash
python -m src.workflow --preset boundary-values --preset negative-scenarios
```

Пресет, который не применим к выбранному типу артефакта, — ошибка.

## 6. Результаты

```text
output/
├── scenarios/
│   └── registration-requirements.md
├── ui-input/           # копии файлов, загруженных через UI
├── generation.json
├── quality-metrics.json
├── report.md
└── charts/
    ├── metrics_boxplot.png
    ├── radar_chart.png
    └── per_scenario_scores.png
```

`quality-metrics.json` — источник правды. В нём реальные `case_summaries`:
идентификатор, тип, заголовок, шаги, ожидаемый результат и требование.

Оба JSON-файла содержат `pipeline_version` — версию контракта стадий. Файлы без
этого ключа читаются как legacy. Если версия не совпадает с текущей, CLI
возвращает код 2 и просит перегенерировать артефакты.

`report.md` содержит:

- общую информацию о прогоне, включая использованные веса `overall`;
- распределение метрик и `overall`;
- таблицу сценариев и оценку «успешный / требует доработки»;
- детализацию по каждому файлу с тест-кейсами;
- графики;
- ошибки (если были);
- источник данных.

## 7. Коды возврата

| Код | Причина | Что делать |
|---:|---|---|
| 0 | успех | — |
| 2 | ошибка входа, нет файла предыдущей фазы | проверить пути и запустить предыдущую фазу |
| 3 | Ollama недоступна или все сценарии упали | проверить `ohw-ollama`, модель, `OLLAMA_BASE_URL` |
| 4 | ошибка записи | проверить права на `output/` |
| 5 | `overall` ниже `--fail-under` | улучшить требования/промпт или снизить порог |
| 130 | Ctrl+C | — |

## 8. Типовые проблемы

### «модель не найдена в Ollama»

```powershell
docker exec ohw-ollama ollama pull qwen2.5:7b-instruct
```

Или перезапустить общий стенд:

```powershell
docker compose -f ..\infra\compose.yaml --profile ollama up -d
```

### «ollama service ... is unreachable»

Проверить контейнер и порт:

```powershell
docker ps --filter name=ohw-ollama
Invoke-RestMethod http://localhost:11434/api/tags
```

### Долго работает

`qwen2.5:7b-instruct` на CPU может отвечать минуты. Уменьшите `--count`,
увеличьте `--timeout`, используйте GPU-профиль `infra/`.

### Много повторов валидации

Модель не соблюдает схему. Уменьшите `--count`, повысьте `--max-retries`,
проверьте, что шаблон и требования согласованы.

## 9. Проверки

```bash
uv run pytest
uv run ruff check src scripts tests
uv run mypy src

# e2e с живой Ollama, источник — SDD проекта
RUN_OLLAMA_INTEGRATION=1 uv run pytest -m integration
```

E2E использует `tests/e2e/input/sdd-requirements.md`, выделенный из `SDD.md`.
