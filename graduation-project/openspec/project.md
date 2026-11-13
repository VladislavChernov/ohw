# Project: graduation-project

Автоматизация генерации и оценки качества тестовой документации с использованием локальной LLM.

## Стек

- Python 3.13, `uv`, lock-файл коммитится.
- HTTP: `httpx`; схемы: `pydantic`; графики: `matplotlib` (backend `Agg`).
- Тесты: `pytest`, `pytest-mock`; статический анализ: `ruff`, `mypy`.
- Инференс: общий контейнер `ohw-ollama` из `infra/`, модель `qwen2.5:7b-instruct`.
- Базовый образ приложения: `ohw-python:3.13` из `infra/python`.
- Общая сеть: `ohw_net` (external), Ollama доступен как `http://ohw-ollama:11434`.
- Код и комментарии — на английском; документация, CLI-сообщения и отчёты — на русском.
- Отчёты — JSON (источник правды) + Markdown (представление) + PNG-графики.

## Архитектура исходников

Код лежит в существующей структуре `src/`, без установки проекта как wheel:

```text
src/
├── __init__.py
├── cli.py                  # console wrapper
├── config.py               # Settings, env
├── models.py               # QualityMetrics, ScenarioResult, PipelineData
├── generator/              # вендоренное ядро DZ2
│   ├── handlers.py
│   ├── input_doc.py
│   ├── models.py
│   ├── normalizer.py
│   ├── normalizer.toml
│   ├── ollama_client.py
│   ├── pipeline.py
│   ├── presets.py
│   ├── presets/*.toml
│   ├── prompt.py
│   ├── renderer.py
│   ├── service.py
│   ├── templates/*.txt
│   ├── templates/_system/*.txt
│   └── validator.py
├── quality/assessor.py     # 4 метрики
├── visualization/charts.py # boxplot, radar, per-scenario
├── reporting/report_generator.py
├── ui/                     # Streamlit-рабочее место (второй фронт)
│   ├── app.py
│   ├── launcher.py
│   └── __main__.py
└── workflow/
    ├── main.py             # CLI и оркестрация фаз
    └── stages.py           # общие стадии для CLI и UI
```

Запуск: `python -m src.workflow` (или `python -m src.workflow.main`) из корня проекта;
в Docker — `PYTHONPATH=/app`.

Проект **не зависит** от `../dz2` во время выполнения: генератор, промпты, пресеты и
`normalizer.toml` вендорены в `src/generator/`.

## Окружение

- `OLLAMA_BASE_URL` — адрес Ollama (по умолчанию `http://localhost:11434`;
  в compose — `http://ohw-ollama:11434`).
- `OLLAMA_MODEL` — имя модели (по умолчанию `qwen2.5:7b-instruct`).
- `OLLAMA_TIMEOUT` — таймаут запроса в секундах (по умолчанию 600).
- `GRADUATION_PROMPT_DIR` — своя папка шаблонов промптов.
- `GRADUATION_PRESETS_DIR` — своя папка пресетов.
- `GRADUATION_NORMALIZER_CONFIG` — свой `normalizer.toml`.
- `GRADUATION_QUALITY_WEIGHTS` — веса `overall` в формате
  `readability=0.25,clarity_goals=0.25,scenario_coverage=0.30,consistency=0.20`;
  флаг `--quality-weights` важнее env.
- Устаревшие `DZ2_PROMPT_DIR`, `DZ2_PRESETS_DIR`, `AI_TESTGEN_NORMALIZER_CONFIG`
  принимаются как fallback.

## Контейнеры

- Общий стенд поднимается из `infra/`: `./infra/up.sh .` читает `graduation-project/infra.yaml`
  и запускает профиль `ollama` (`ohw-ollama`).
- Приложение собирается из `ohw-python:3.13`, подключается к внешней сети `ohw_net`.
- Собственный Ollama, собственная сеть и собственные порты не создаются.
- GPU/AMD-режим настраивается на уровне `infra/` (`./infra/up.sh . --gpu` / `--amd`).

## Границы

- Проект не меняет `dz2/src`, `dz2/prompts`, `dz2/presets`, `dz2/normalizer.toml`.
- Проект не включает DZ6 (проверка и оптимизация автотестов).
- API-тесты не входят в проект.
- Streamlit-рабочее место (`src/ui/`) — второй фронт поверх тех же стадий;
  бизнес-логика генерации, метрик и отчёта не дублируется.
- Модель не обучается; LLM-судья текста не используется как метрика.
- Нормализация требований детерминированная (DZ2 normalizer), до сборки промпта.
- Embedded Python не вендорится; переносимый запуск — через `uv`.

## Связь с домашними заданиями

- **DZ2**: генерация тест-кейсов и пяти типов артефактов; нормализатор требований,
  шаблоны промптов, пресеты, валидация и рендеринг вендорены в проект.
- **DZ7**: шаблоны промптов для пяти типов артефактов (используются вендоренные копии
  в `src/generator/templates/`).
- **DZ8**: идея оценки качества; метрики переработаны и реализованы заново в
  `src/quality/assessor.py`.
- **Infra**: общий `ohw-ollama`, образ `ohw-python:3.13`, сеть `ohw_net`.
- **DZ3 advanced**, **DZ6** и API-тесты в выпускной проект не входят.

## Активные изменения

- `openspec/changes/add-guard-tests/` — guard-тесты документации и лаунчеров.
- `openspec/changes/add-streamlit-workbench/` — Streamlit-рабочее место.
- `openspec/changes/harden-pipeline/` — версия контракта и per-file recovery.
- `openspec/changes/quality-weights/` — настраиваемые веса `overall`.
