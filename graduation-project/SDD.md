# Software Design Document (SDD)
# Выпускной проект: автоматическая генерация и оценка тестовой документации с локальной LLM

## Информация о документе

| Поле | Значение |
|---|---|
| Название | Software Design Document |
| Проект | `graduation-project` |
| Версия | 0.1.0 |
| Автор | Владислав Чернов |
| Дата | 11.11.2026 |
| Статус | Реализовано, документация актуализирована |
| Связанные ДЗ | DZ2, DZ7, DZ8 (DZ6 не входит) |

---

## 1. Общие сведения

### 1.1 Назначение

Документ описывает фактическую архитектуру и поведение выпускного проекта:
генерацию тестовой документации локальной моделью Ollama, оценку качества
четырьмя детерминированными метриками, визуализацию и отчётность.

Проект собран в одной папке `graduation-project`. Во время выполнения он не
импортирует `../dz2`, `../dz7` или `../dz8`: нужное ядро генерации вендорено в
`src/generator/`, шаблоны и пресеты лежат рядом с кодом. Общие ресурсы — только
контейнеры из `infra/` (`ohw-ollama`, образ `ohw-python:3.13`, сеть `ohw_net`).

### 1.2 Область применения

- Генерация пяти типов артефактов: `testcases`, `checklist`, `testplan`,
  `qareport`, `coveragematrix`.
- Нормализация требований, сборка промпта, вызов Ollama, валидация, retry,
  рендеринг Markdown.
- Оценка качества: читаемость, чёткость целей, покрытие требований,
  консистентность, взвешенный `overall`.
- Визуализация: boxplot, radar, оценки по сценариям.
- Отчётность: JSON (источник правды) + Markdown + PNG.
- CLI-пайплайн с возможностью запускать фазы отдельно.
- Streamlit-рабочее место: требования, промпт, генерация, метрики и отчёт
  поверх тех же стадий, что и CLI.

Вне области: DZ6, обучение модели, LLM-судья, собственный Ollama и собственные
постоянные контейнеры. API-тесты в проект не входят.

### 1.3 Определения

- **BR-N** — идентификатор бизнес-требования, например `BR-1`.
- **TC-N** — идентификатор тест-кейса, например `TC-01`.
- **Сценарий** — один входной файл требований, обработанный генератором.
- **Run** — один вызов модели для сценария; `--runs N` задаёт число вызовов.
- **Overall** — взвешенное среднее известных метрик.
- **`None` / `n/a`** — метрика не измерима; это не ноль.
- **EARS** — нотация требований `SHALL` + `WHEN/THEN`.
- **OpenSpec** — структура `openspec/project.md`, `changes/`, `specs/`.

### 1.4 Ссылки

- OpenSpec проекта: `openspec/`.
- Общий стенд: `infra/compose.yaml`, `infra/python/Dockerfile`.
- Ollama API: <https://github.com/ollama/ollama/blob/main/docs/api.md>.
- DZ2 (источник вендоренного ядра): `../dz2/`.
- DZ7 (источник шаблонов): `../dz7/`.
- DZ8 (идея метрик): `../dz8/`.

---

## 2. Архитектура

### 2.1 Верхнеуровневая схема

```text
┌─────────────────────────────────────────────────────────────┐
│ CLI: python -m src.workflow                                 │
│ argparse → выбор фаз → коды возврата                        │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌───────────────────────┐     ┌──────────────────────────────┐
│ src/workflow/main.py  │────▶│ src/generator/pipeline.py    │
│ generate/assess/      │     │ load → prompt → Ollama →     │
│ visualize/report      │     │ validate/retry → render      │
└───────────────────────┘     └──────────────────────────────┘
          │                              │
          │ generation.json              │ scenarios/*.md
          ▼                              ▼
┌───────────────────────┐     ┌──────────────────────────────┐
│ src/quality/assessor  │     │ src/reporting/report_generator│
│ 4 метрики + overall   │     │ JSON + Markdown              │
└───────────────────────┘     └──────────────────────────────┘
          │ quality-metrics.json
          ├──────────────────────────────┐
          ▼                              ▼
┌───────────────────────┐     ┌──────────────────────────────┐
│ src/visualization     │     │ report.md                    │
│ charts/*.png          │     │                              │
└───────────────────────┘     └──────────────────────────────┘
```

### 2.2 Компоненты

| Компонент | Ответственность |
|---|---|
| `src/cli.py` | тонкая обёртка для точки входа |
| `src/config.py` | `Settings`, значения по умолчанию, env |
| `src/models.py` | `QualityMetrics`, `ScenarioResult`, `PipelineData`, сериализация |
| `src/generator/` | вендоренное ядро DZ2: загрузка, нормализация, промпты, пресеты, Ollama, валидация, retry, рендер |
| `src/quality/assessor.py` | детерминированные метрики и агрегация |
| `src/visualization/charts.py` | графики matplotlib (`Agg`) |
| `src/reporting/report_generator.py` | JSON + Markdown |
| `src/ui/` | Streamlit-рабочее место |
| `src/workflow/stages.py` | общие стадии для CLI и UI |
| `src/workflow/main.py` | CLI, фазы, проверка модели, коды возврата |
| `scripts/cli.py` | совместимый запуск `python scripts/cli.py` |

### 2.3 Поток данных и артефакты фаз

```text
input/*.md|*.txt
  └─ generate ─▶ output/scenarios/<stem>.md
                 output/generation.json
  └─ assess   ─▶ output/quality-metrics.json
  └─ visualize ─▶ output/charts/*.png
  └─ report   ─▶ output/report.md
```

`generation.json` хранит rendered-текст, все варианты генераций, тест-кейсы,
промпт, метаданные и `pipeline_version`. Это позволяет пересчитать метрики без
повторного вызова модели. `quality-metrics.json` — источник правды для графиков
и Markdown, он также содержит `pipeline_version`. Markdown никогда не парсится
пайплайном.

Версия контракта — единая константа `PIPELINE_VERSION` в `src/config.py`.
Файлы без ключа читаются как legacy; несовпадение версии — ошибка входа с
понятным сообщением.

Фазы можно запускать отдельно. Без флагов выполняются все четыре в порядке
`generate → assess → visualize → report`.

---

## 3. Модули

### 3.1 `src/generator/`

Вендоренная копия DZ2 с импортами `src.generator.*`.

| Модуль | Назначение |
|---|---|
| `input_doc.py` | `.md`/`.txt`, front-matter `type`, URL, `load_docs` |
| `normalizer.py` | кодировки, `BR-N`, URL, детерминированная нормализация |
| `normalizer.toml` | настройки нормализатора по умолчанию |
| `prompt.py` | шаблоны `templates/<type>.txt`, `_system/*.txt` |
| `presets.py` | `presets/*.toml`, выбор по типу артефакта |
| `handlers.py` | тип артефакта → system/user/retry prompt + валидатор |
| `validator.py` | JSON-схема кейсов, Markdown-разделы, таблицы |
| `renderer.py` | итоговый Markdown |
| `ollama_client.py` | `/api/chat`, `/api/tags`, ошибки |
| `service.py` | один артефакт: prompt → chat → validate → retry → render |
| `pipeline.py` | один файл требований → `ScenarioResult`, `--runs N` |

Порядок разрешения ресурсов:

- шаблоны: `--prompt-dir` → `GRADUATION_PROMPT_DIR` → `./templates` →
  `src/generator/templates`;
- пресеты: `--presets-dir`/`GRADUATION_PRESETS_DIR` → `./presets` →
  `src/generator/presets`;
- `normalizer.toml`: `GRADUATION_NORMALIZER_CONFIG` → `./normalizer.toml` →
  `src/generator/normalizer.toml`.

Устаревшие `DZ2_PROMPT_DIR`, `DZ2_PRESETS_DIR`, `AI_TESTGEN_NORMALIZER_CONFIG`
принимаются как fallback.

### 3.2 `src/quality/assessor.py`

Чистые функции без сети и без LLM:

- `strip_markdown` — удаляет code fences, разделители таблиц, разметку;
- `assess_readability` — средняя длина предложения;
- `assess_clarity_goals` — пять категорий измеримых формулировок;
- `extract_requirement_ids` / `covered_requirement_ids` — `BR-N`;
- `assess_scenario_coverage` — покрытие требований;
- `assess_consistency` — Jaccard повторных генераций;
- `assess_internal_consistency` — эвристика для одного run;
- `weighted_overall` — взвешенное среднее с перенормировкой;
- `assess_quality` — все метрики одного сценария;
- `aggregate_metrics` — средние по сценариям с сохранением `None`.

### 3.3 `src/visualization/charts.py`

- `create_boxplot` — распределение метрик по сценариям;
- `create_radar_chart` — средние метрики (нужно ≥ 3 известных метрик);
- `create_per_scenario_chart` — сгруппированные столбцы по сценариям;
- `create_all_charts` — создаёт применимые графики и возвращает относительные
  пути `charts/<file>.png`.

Backend `Agg` выбирается до импорта `pyplot`; графики не требуют дисплея.
Неизмеримая метрика не рисуется как ноль: в grouped bars используется `NaN`.

### 3.4 `src/reporting/report_generator.py`

- `write_json_report` — `quality-metrics.json`;
- `render_markdown` / `write_markdown_report` — `report.md`;
- `generate_all_reports` — оба файла;
- `load_pipeline_data` — чтение JSON для фаз `visualize`/`report` с проверкой
  `pipeline_version`.

JSON содержит реальные `case_summaries` (`id`, `type`, `title`, `steps`,
`expected`, `requirement`), метаданные прогона, средние, ошибки и пути графиков.

Markdown содержит обязательные разделы: общая информация, распределение
качества, список сценариев, детализация по сценариям, визуальный отчёт,
ошибки, источник данных. Неизмеримые значения печатаются как `n/a`.

### 3.5 `src/workflow/main.py` и `src/workflow/stages.py`

`stages.py` — файловые стадии без `argparse` и Streamlit:

- `generate` — сценарии, `generation.json`;
- `assess` — пересчёт метрик, `quality-metrics.json`;
- `visualize` — графики;
- `report` — `report.md` и JSON;
- `write_snapshot` / `load_snapshot` — снимок генерации и проверка версии.

`main.py` добавляет CLI и логирование:

- `build_parser` — CLI;
- `selected_phases` — выбранные фазы в каноническом порядке;
- `_run_generate` — проверка модели, `continue_on_error`, вызов `stages.generate`;
- `_run_assess` — `stages.assess`;
- `_run_visualize` — `stages.visualize`;
- `_run_report` — `stages.report`;
- `main` — обработка ошибок, коды возврата, `--fail-under`.

### 3.6 `src/models.py`

- `QualityMetrics` — четыре метрики + `overall`, `to_dict`/`from_dict`;
- `ScenarioResult` — результат одного файла; snapshot/report сериализация;
- `PipelineData` — метаданные, сценарии, ошибки, средние, графики;
  `refresh_averages`, `to_snapshot_dict`/`from_snapshot_dict`,
  `to_report_dict`/`from_report_dict`.

Порог успешности сценария: `overall ≥ 0.5`.

### 3.7 `src/ui/`

- `app.py` — Streamlit-рабочее место: пять вкладок и боковая панель;
- `launcher.py` — `streamlit run` с адресом `0.0.0.0:8501`;
- `__main__.py` — `python -m src.ui`.

UI вызывает `src.workflow.stages`, поэтому результаты совпадают с CLI. Загрузки
копируются в `output/ui-input/`, а `PipelineData` живёт в `st.session_state`.
Вкладка «Промпт» собирает запрос из шаблона DZ7 и пресетов; отредактированный
промпт передаётся в `GenerationRequest.prompts` и применяется к выбранному
файлу.

---

## 4. Интерфейсы

### 4.1 CLI

```text
python -m src.workflow [фазы] [параметры]
```

| Флаг | По умолчанию | Назначение |
|---|---|---|
| `--generate` | — | сгенерировать артефакты |
| `--assess` | — | пересчитать метрики из `generation.json` |
| `--visualize` | — | построить графики |
| `--report` | — | записать `report.md` |
| `--input-dir` | `input` | папка требований |
| `--type` | `testcases` | тип артефакта |
| `--count` | `10` | кейсов на файл |
| `--runs` | `1` | повторов на сценарий |
| `--temperature` | `0.7` | температура модели |
| `--max-retries` | `3` | повторы при невалидном ответе |
| `--url` | — | URL тестируемого сайта |
| `--normalize` | выкл. | нормализация требований |
| `--preset` | — | id пресета, можно несколько |
| `--prompt-dir` | — | своя папка шаблонов |
| `--base-url` | env/`localhost:11434` | адрес Ollama |
| `--model` | env/`qwen2.5:7b-instruct` | модель |
| `--timeout` | env/`600` | таймаут, секунды |
| `--output-dir` | `output` | папка результатов |
| `--continue-on-error` | выкл. | продолжать при недоступности Ollama для отдельного файла |
| `--fail-under` | — | порог качества |

Без флагов фаз выполняются все четыре фазы.

### 4.2 Переменные окружения

| Переменная | По умолчанию |
|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` |
| `OLLAMA_TIMEOUT` | `600` |
| `GRADUATION_PROMPT_DIR` | `src/generator/templates` |
| `GRADUATION_PRESETS_DIR` | `src/generator/presets` |
| `GRADUATION_NORMALIZER_CONFIG` | `src/generator/normalizer.toml` |

### 4.3 Коды возврата

| Код | Значение |
|---:|---|
| 0 | успех |
| 2 | ошибка входа/CLI, нет файла предыдущей фазы |
| 3 | Ollama недоступна, ни один сценарий не сгенерирован |
| 4 | ошибка записи отчёта/графиков |
| 5 | `overall < --fail-under` |
| 130 | прерывание пользователем |

### 4.4 Python-интерфейсы

```python
from src.generator.pipeline import GenerationRequest, generate_scenarios
from src.generator.ollama_client import OllamaClient
from src.quality.assessor import assess_quality
from src.reporting.report_generator import generate_all_reports
from src.visualization.charts import create_all_charts
from src.workflow import stages
from src.workflow.main import main
```

### 4.5 Streamlit UI

```bash
uv run python -m src.ui                 # http://localhost:8501
docker compose up -d ui                 # тот же адрес
```

UI и CLI вызывают одни и те же функции `src.workflow.stages`; отдельного API
между фронтами нет. Вкладки: требования DZ2, промпт DZ7, генерация DZ2,
метрики DZ8, отчёт. DZ6 и API-тесты в проект не входят.

---

## 5. Метрики

### 5.1 Readability

```text
avg = total_words / sentence_count
readability = clamp(1 - |avg - 17| / 30, 0, 1)
```

Предложения делятся по `[.!?…]+`; Markdown-разметка, code fences и разделители
таблиц удаляются. Пустой текст → `0.0`.

### 5.2 Clarity of goals

Пять независимых категорий, каждая добавляет `0.2`:

1. модальность: `должен/должна/должно/должны/must/shall/should`;
2. ожидание: `ожидаем*/expected`;
3. проверка: `провер*/убеди*/верифиц*/verify`;
4. запрет: `не должен/нельзя/must not`;
5. результат: `результат*/поведени*/сообщени*/result/behavior/output`.

### 5.3 Scenario coverage

```text
coverage = |expected ∩ found| / |expected|
```

`expected` — уникальные `BR-N` входного файла. Если их нет — `None`.
Если есть, но в результате нет ссылок — честный `0.0`.

### 5.4 Consistency

При `runs ≥ 2` — среднее попарных Jaccard-близостей множеств слов (длина > 2):

```text
J(A, B) = |A ∩ B| / |A ∪ B|
```

При `runs = 1` — внутренняя эвристика: `0.8`, если номера `BR-N` образуют
непрерывную последовательность, иначе `0.5`. Это не воспроизводимость, и отчёт
показывает `runs = 1`.

### 5.5 Overall

```text
weights = {readability: 0.25, clarity: 0.25, coverage: 0.30, consistency: 0.20}
overall = Σ(w_i × metric_i) / Σ(w_i)   # только известные метрики
```

Если известных метрик нет — `0.0`. `None` никогда не подменяется нулём.
Сценарий успешен при `overall ≥ 0.5`.

---

## 6. Форматы данных

### 6.1 `generation.json`

```json
{
  "pipeline_version": "0.1.0",
  "input_dir": "input",
  "artifact_type": "testcases",
  "model": "qwen2.5:7b-instruct",
  "runs": 2,
  "count": 4,
  "temperature": 0.2,
  "normalize": false,
  "presets": [],
  "generated_at": "2026-11-11T10:00:00+00:00",
  "scenarios": [
    {
      "filename": "requirements.md",
      "source_path": "input/requirements.md",
      "artifact_type": "testcases",
      "output_file": "output/scenarios/requirements.md",
      "rendered": "# Тест-кейсы ...",
      "generations": ["...", "..."],
      "cases": [{"id": "TC-01", "type": "positive", "...": "..."}],
      "prompt": "...",
      "attempts": 1,
      "quality": {"readability": 0.8, "overall": 0.7},
      "requirement_ids": ["BR-1"],
      "covered_requirements": ["BR-1"],
      "prompt_sha256": "...",
      "runs": 2,
      "error": null
    }
  ],
  "errors": []
}
```

### 6.2 `quality-metrics.json`

```json
{
  "project": "graduation-project",
  "version": "0.1.0",
  "pipeline_version": "0.1.0",
  "input_dir": "input",
  "artifact_type": "testcases",
  "model": "qwen2.5:7b-instruct",
  "runs": 2,
  "count": 4,
  "temperature": 0.2,
  "normalize": false,
  "presets": [],
  "generated_at": "2026-11-11T10:00:00+00:00",
  "total_scenarios": 1,
  "successful": 1,
  "unsuccessful": 0,
  "quality_threshold": 0.5,
  "averages": {"readability": 0.8, "scenario_coverage": 1.0, "overall": 0.7},
  "charts": {"boxplot": "charts/metrics_boxplot.png"},
  "errors": [],
  "scenarios": [
    {
      "id": 1,
      "filename": "requirements.md",
      "artifact_type": "testcases",
      "output_file": "output/scenarios/requirements.md",
      "quality": {"readability": 0.8, "overall": 0.7},
      "requirement_ids": ["BR-1"],
      "cases_with_requirements": ["BR-1"],
      "prompt_sha256": "...",
      "attempts": 1,
      "runs": 2,
      "case_summaries": [
        {
          "id": "TC-01",
          "type": "positive",
          "title": "Успешная регистрация",
          "preconditions": [],
          "steps": ["..."],
          "expected": "...",
          "requirement": "BR-1"
        }
      ],
      "error": null
    }
  ]
}
```

### 6.3 Версионирование и совместимость

`pipeline_version` описывает совместимость файлов между фазами. Правила чтения:

| Состояние | Поведение |
|---|---|
| ключа нет | legacy-файл, читается |
| версия совпадает | файл читается |
| версия отличается | `ValueError`, код 2, файл не читается |

`version` в `quality-metrics.json` — версия проекта/схемы отчёта; она не
заменяет `pipeline_version`. Старые файлы не мигрируются автоматически: при
следующем несовместимом изменении их нужно перегенерировать.

### 6.4 `report.md`

Обязательные разделы:

1. `# Отчёт о качестве генерации тестовой документации`
2. `## Общая информация`
3. `## Распределение качества`
4. `## Список сценариев`
5. `## Детализация по сценариям`
6. `## Визуальный отчёт`
7. `## Ошибки` (при наличии)
8. `## Источник данных`

### 6.5 Графики

| Ключ | Файл | Условие |
|---|---|---|
| `boxplot` | `charts/metrics_boxplot.png` | есть хотя бы одна метрика |
| `radar` | `charts/radar_chart.png` | известны ≥ 3 средних метрики |
| `per_scenario` | `charts/per_scenario_scores.png` | есть метрики у сценариев |

---

## 7. Обработка ошибок

| Исключение | Поведение |
|---|---|
| `InputError` | код 2, сообщение о файле/папке |
| `PromptError` / `PresetError` | код 2 |
| `FileNotFoundError` (нет снимка/отчёта) | код 2 с подсказкой запустить предыдущую фазу |
| `OllamaConnectionError` | без `--continue-on-error` — код 3 и прерывание; с флагом — ошибка файла, остальные продолжаются |
| `OllamaResponseError` | ошибка сценария или код 3, если не сгенерирован ни один |
| `GenerationError` | ошибка одного сценария, остальные продолжаются |
| `ValueError` при несовпадении `pipeline_version` | код 2 с именем файла и версиями |
| `OSError` при записи | код 4 |
| `KeyboardInterrupt` | код 130 |

Ошибки сценариев попадают в `errors` и в раздел «Ошибки» отчёта.
`--fail-under` проверяется после успешного отчёта.

---

## 8. Тестирование

### 8.1 Unit-тесты

`tests/`:

- `test_quality.py` — формулы, `None`-семантика, агрегация;
- `test_models.py` — snapshot/report-сериализация;
- `test_reporting.py` — JSON как источник правды, `n/a`, реальные кейсы;
- `test_generator.py` — шаблоны, промпты, валидатор, рендер, загрузка;
- `test_workflow.py` — выбор фаз, assess/report по файлам, версии и коды возврата;
- `test_pipeline.py` — fail-fast и per-file recovery при ошибке Ollama;
- `test_charts.py` — создание графиков без дисплея.

Команда:

```bash
uv run pytest
```

Unit-тесты не требуют Ollama. Результат на момент документирования:
`105 passed, 1 skipped`.

### 8.2 Integration / e2e

`tests/test_integration_ollama.py` включается переменной
`RUN_OLLAMA_INTEGRATION=1` и использует живой `ohw-ollama`.

Источник требований для e2e — **SDD этого проекта**: из него выделены
бизнес-требования `BR-N` в `tests/e2e/input/sdd-requirements.md`. Это проверяет
генерацию на реальном документе проекта, а не на абстрактном примере.

```bash
RUN_OLLAMA_INTEGRATION=1 OLLAMA_BASE_URL=http://localhost:11434 \
  uv run pytest -m integration
```

В Docker внутри `ohw_net`:

```bash
docker run --rm --network ohw_net -v "$PWD:/repo" -w /repo \
  -e OLLAMA_BASE_URL=http://ohw-ollama:11434 \
  -e RUN_OLLAMA_INTEGRATION=1 \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim uv run pytest -m integration
```

Результат прогона: `1 passed, 105 deselected in 147.99s`. Средний `overall` на
требованиях из SDD — `0.399`; это ожидаемо для плотного формального текста.
Проверяется структура e2e: генерация, метрики, графики и отчёт созданы.

### 8.3 Статический анализ

```bash
uv run ruff check src scripts tests
uv run mypy src
```

Оба анализа должны проходить без ошибок.

### 8.4 Живой прогон

Полный пайплайн на общей модели:

```bash
python -m src.workflow --input-dir input --output-dir output \
  --count 4 --runs 2 --temperature 0.2
```

Проверяется наличие `scenarios/*.md`, `generation.json`,
`quality-metrics.json`, `report.md`, `charts/*.png`.

---

## 9. Развёртывание

### 9.1 Общий стенд

`infra.yaml`:

```yaml
components:
  - ollama
```

Запуск:

```bash
./infra/up.sh graduation-project          # CPU
./infra/up.sh graduation-project --gpu    # NVIDIA
./infra/up.sh graduation-project --amd    # AMD ROCm
```

`ohw-ollama` слушает `localhost:11434` на хосте и `ohw-ollama:11434` в `ohw_net`.
Модель `qwen2.5:7b-instruct` скачивается один раз в volume `ohw_ollama_models`.

### 9.2 Docker-образ приложения

`Dockerfile`:

```dockerfile
FROM ohw-python:3.13
ENV PYTHONPATH=/app
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY input ./input
COPY scripts ./scripts
ENTRYPOINT ["uv", "run", "--no-dev", "python", "-m", "src.workflow.main"]
```

Проект не устанавливается как wheel (`[tool.uv] package = false`).

Один образ обслуживает оба фронта: `streamlit` входит в основные зависимости,
поэтому CLI запускается прежним entrypoint, а UI — тем же образом с
`entrypoint: python -m src.ui`.

### 9.3 Compose

`compose.yaml` содержит сервисы `app` (CLI) и `ui` (Streamlit, порт `8501`),
внешнюю сеть `ohw_net` и переменные
`OLLAMA_BASE_URL=http://ohw-ollama:11434`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`.
Собственный Ollama, model-pull и собственные постоянные контейнеры не
создаются.

### 9.4 Скрипты запуска

- `run.ps1` (Windows PowerShell), `run.cmd` (cmd), `run.sh` (Linux/macOS);
- если сети `ohw_net` нет, скрипт поднимает `infra/compose.yaml --profile ollama`;
- затем `docker compose up --build --abort-on-container-exit
  --exit-code-from app`;
- результаты монтируются в `./output`, требования — из `./input`.

### 9.5 Локальный запуск без Docker

```bash
uv sync --dev
uv run python -m src.workflow --input-dir input --output-dir output
```

Нужна запущенная Ollama на `localhost:11434` с моделью `qwen2.5:7b-instruct`.

---

## 10. Ограничения и безопасность

- Модель работает локально; данные требований не отправляются во внешние сервисы.
- Секретов в репозитории нет; `.env` в `.gitignore`.
- Приложение не публикует портов, кроме UI (`8501`) при явном
  `docker compose up ui`; наружу смотрит только `ohw-ollama` (11434).
- Входные файлы читаются как данные, а не исполняемый код.
- Метрики детерминированные и воспроизводимые; LLM-судья не используется.
- `--runs 1` даёт лишь внутреннюю эвристику консистентности.
- Качество текста ограничено моделью `qwen2.5:7b-instruct` и шаблонами промптов.
- Проект не заменяет ревью тест-документации человеком.
- Старые артефакты без `pipeline_version` читаются как legacy и не мигрируются
  автоматически.
- Embedded Python не поставляется; переносимый запуск — через `uv python install`.

---

## 11. Соответствие OpenSpec

| Документ | Содержание |
|---|---|
| `openspec/project.md` | стек, окружение, контейнеры, границы |
| `openspec/changes/proposal.md` | объём, что входит и что нет |
| `openspec/changes/design.md` | архитектура, поток данных, CLI, ошибки |
| `openspec/changes/tasks.md` | чек-лист реализации |
| `openspec/specs/generation/spec.md` | EARS-требования к генерации |
| `openspec/specs/pipeline/spec.md` | версия контракта и per-file recovery |
| `openspec/specs/quality/spec.md` | EARS-требования и формулы метрик |
| `openspec/changes/add-guard-tests/` | guard-тесты документации и лаунчеров |
| `openspec/changes/harden-pipeline/` | P0: версионирование и обработка ошибок |

Документы описывают реализованное поведение. Изменения поведения должны
сопровождаться обновлением соответствующей спеки и этого SDD.

---

## 12. История документа

| Версия | Дата | Изменение |
|---|---|---|
| 0.1 | 09.11.2026 | Черновик `SDD.md` |
| 0.2 | 10.11.2026 | Подробный `SDD_new.md` с псевдокодом |
| 0.3 | 11.11.2026 | Документы сведены в один `SDD.md`, приведены к фактической реализации: `src.*`, вендоринг DZ2, общий `infra`, CLI-фазы, метрики, e2e на SDD |
| 0.4 | 11.11.2026 | Добавлен раздел о шве API-тестов, guard-тестах и решении по embedded Python |
| 0.5 | 12.11.2026 | Добавлено Streamlit-рабочее место: общие стадии, сервис `ui`, тесты AppTest |
| 0.6 | 12.11.2026 | Добавлена вкладка промпта (DZ7) с правкой и передачей в генерацию; вкладки отмечены по источникам ДЗ |
| 0.7 | 12.11.2026 | Шов API-тестов удалён: API-тесты не входят в проект, UI оставлен с пятью вкладками |
| 0.8 | 13.11.2026 | P0 ревью: `pipeline_version` в артефактах, legacy/mismatch, `--continue-on-error` и per-file recovery |
