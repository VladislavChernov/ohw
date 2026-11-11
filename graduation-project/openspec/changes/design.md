# Архитектура выпускного проекта

## Поток данных

```text
input/*.md|*.txt
      │
      ▼
┌──────────────────────────────────────────────┐
│ generate                                     │
│ load_docs → normalize? → prompt → Ollama     │
│ → validate/retry → render → assess_quality   │
│ → scenarios/*.md + generation.json           │
└──────────────────────────────────────────────┘
      │ generation.json (rendered + runs + cases)
      ▼
┌──────────────────────────────────────────────┐
│ assess                                       │
│ пересчёт 4 метрик → quality-metrics.json     │
└──────────────────────────────────────────────┘
      │ quality-metrics.json (источник правды)
      ├──────────────────────────────┐
      ▼                              ▼
┌──────────────────┐        ┌──────────────────┐
│ visualize        │        │ report           │
│ charts/*.png     │        │ report.md        │
└──────────────────┘        └──────────────────┘
```

Фазы общаются только через файлы в `--output-dir`, поэтому каждую можно запускать
отдельно. Без флагов выполняются все четыре фазы в каноническом порядке.

## Модули

### `src/generator/` — вендоренное ядро DZ2

- `input_doc.py` — сбор `.md`/`.txt`, front-matter `type`, URL, нормализация.
- `normalizer.py` + `normalizer.toml` — детерминированное приведение требований,
  кодировок, идентификаторов `BR-N`.
- `prompt.py` — внешние шаблоны `templates/<type>.txt` и `_system/*.txt`.
- `presets.py` + `presets/*.toml` — дополнительные инструкции в `{extra}`.
- `handlers.py` — связка «тип артефакта → промпт → валидатор».
- `validator.py` — JSON-схема тест-кейсов и проверки Markdown-разделов.
- `service.py` — один артефакт: prompt, chat, validate, retry, render.
- `renderer.py` — итоговый Markdown.
- `ollama_client.py` — `/api/chat`, `/api/tags`, ошибки соединения и ответа.
- `pipeline.py` — один файл требований → `ScenarioResult`, `--runs N`.

### `src/quality/assessor.py`

Четыре детерминированные метрики, агрегация и взвешенный `overall`.
`None` означает «не измеримо» и никогда не заменяется нулём.

### `src/visualization/charts.py`

`matplotlib` с backend `Agg`; boxplot, radar, grouped bars. Если метрик нет,
соответствующий график не создаётся.

### `src/reporting/report_generator.py`

JSON-отчёт с реальными `case_summaries` и Markdown-отчёт. Markdown не парсится
пайплайном.

### `src/workflow/main.py`

CLI, выбор фаз, проверка модели, оркестрация, коды возврата.

## Хранилище результатов

| Файл | Назначение |
|---|---|
| `output/generation.json` | снимок генерации: rendered, все runs, cases, prompt |
| `output/quality-metrics.json` | источник правды для графиков и отчёта |
| `output/scenarios/<stem>.md` | сгенерированный артефакт |
| `output/report.md` | Markdown-отчёт |
| `output/charts/*.png` | графики |

## Ошибки и коды возврата

| Код | Значение |
|---:|---|
| 0 | успех |
| 2 | ошибка входа/CLI/отсутствует файл предыдущей фазы |
| 3 | Ollama недоступна или ни один сценарий не сгенерирован |
| 4 | ошибка записи отчёта/графиков |
| 5 | средний `overall` ниже `--fail-under` |
| 130 | прерывание пользователем |

Ошибка одного файла не останавливает остальные: она попадает в `errors`,
успешные сценарии продолжают обрабатываться. Ошибка соединения с Ollama
прерывает прогон.

## CLI

```text
python -m src.workflow [--generate] [--assess] [--visualize] [--report]
    --input-dir input --output-dir output --type testcases
    --count 10 --runs 1 --temperature 0.7 --max-retries 3
    --url URL --normalize --preset ID [--preset ID ...]
    --prompt-dir DIR --base-url URL --model NAME --timeout SEC
    --fail-under SCORE
```

## Развёртывание

- `infra.yaml` объявляет компонент `ollama`.
- `Dockerfile` наследует `ohw-python:3.13`, ставит зависимости через `uv sync`.
- `compose.yaml` содержит только сервис `app`, внешнюю сеть `ohw_net` и переменные
  `OLLAMA_BASE_URL=http://ohw-ollama:11434`.
- `run.ps1` / `run.cmd` / `run.sh` сами поднимают общий стенд, если сети `ohw_net`
  ещё нет, затем запускают compose.
- GPU/AMD выбирается в `infra/`, а не в проекте.
