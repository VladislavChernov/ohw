# DZ3 — Генератор API-автотестов через локальную LLM

Учебное домашнее задание курса OTUS. Цель — научить локальную LLM (Ollama)
проектировать автотесты на REST API: от простой генерации pytest-кода до
структурированного плана, который исполняет собственное детерминированное ядро.

Проект состоит из **двух вариантов** одной идеи — от «маленького» учебного до
«продвинутого» эталонного.

## Варианты

| Вариант | Суть | Куда |
|---|---|---|
| **simple** | «Для самых маленьких»: LLM пишет **pytest-код**, пайплайн его сохраняет и запускает. Простой и явный, весь пайплайн на виду. | [`simple/`](simple/README.md) |
| **advanced** | LLM выдаёт **JSON-план тестов (данные, не код)**, а исполняет его отдельное детерминированное **ядро** (только HTTP + проверки + отчёт, без pytest в рантайме). | [`advanced/`](advanced/README.md) |

> Чем отличаются варианты и почему advanced — «эволюция идеи», см.
> человекочитаемый обзор: [`advanced/docs/overview.md`](advanced/docs/overview.md).

## Общая библиотека и инфраструктура

Оба варианта работают с локальной LLM из общей инфраструктуры и переиспользуют
общий код:

- **`ohw-kit`** — общие блоки, выделенные из задач курса (LLM-клиент, оценщик
  условий, чтение контрактов, рендер отчёта). Advanced зависит от него
  (`ohw-kit @ file:///.../kit`); simple — учебный и свой код не трогает.
- **`infra`** — единый сервис `ohw-ollama` (модель `qwen2.5:7b-instruct`),
  переиспользуется проектами; задаётся в [`infra.yaml`](infra.yaml) → запуск
  через [`../infra/up.sh`](../infra/up.sh).

## Способы запуска

Разработка ведётся в **devcontainer** (`.devcontainer/` каждого варианта), а
«продовый» прогон — в Docker. Но любой вариант можно запустить и на машине
с обычным системным Python — стека devcontainer не требуется.

### 1. Devcontainer (основной способ)

Откройте `ohw/dz3` в VS Code → «Reopen in Container». Инфраструктура
(`ohw-ollama`) уже доступна контейнеру по `http://host.docker.internal:11434`.
Внутри — те же команды, что и локально (`uv sync --dev`, `uv run ...`).

### 2. Локально, системный Python (Linux / macOS / Windows)

Требуется: Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker
(только ради общего ollama — сам код локально ходит на `localhost:11434`).

```bash
# 1) поднять общий ollama (кроссплатформенный bash-скрипт инфры)
cd ohw/infra && ./up.sh ../dz3 && cd ../..

# 2) запустить advanced локально (uv сам создаст venv и поставит kit по file://)
cd ohw/dz3/advanced
uv sync --dev
uv run json-testgen-advanced

# simple (если нужен):
cd ../simple && uv sync --dev && uv run api-testgen
```

Если ollama уже слушает не на `localhost:11434`, переопределите адрес:
`OLLAMA_BASE_URL=http://host.docker.internal:11434 uv run json-testgen-advanced`.

### 3. Docker (E2E, как в CI)

```bash
# 1) shared ollama на сети ohw_net
cd ohw/infra && ./up.sh ../dz3

# 2) app-контейнер advanced на той же сети; input/output монтируются из dz3/advanced
cd ohw/dz3
docker compose up --build
```

Отчёт появится в `ohw/dz3/advanced/output/report.md`, план — в `plan.json`.
Генерация плана на CPU занимает несколько минут (`OLLAMA_TIMEOUT` по умолчанию
1200 с); для GPU запустите инфру с `./up.sh ../dz3 --gpu`.

## Быстрый старт

Кратко (подробности и варианты запуска — ниже, в «Способы запуска»):

```bash
cd ohw/dz3/advanced
uv sync --dev
uv run json-testgen-advanced      # перед этим: ohw/infra/up.sh ../dz3
```

## Разработка (качество)

Каждый вариант проверяется своим тулчейном:

```bash
uv run pytest -q        # юнит-тесты (без сети)
uv run ruff check src tests
uv run mypy src
```

- **simple**: 22 теста — green.
- **advanced**: 50 тестов — green.

## Структура

```
dz3/
├── simple/       LLM → pytest-код → прогон (учебный, «для самых маленьких»)
├── advanced/     LLM → JSON-план → детерминированное ядро → отчёт (эталонный)
│   └── docs/     читабельный обзор проекта (для QA/менеджеров)
└── openspec/     спецификации изменений (spec-first, bundles в openspec/changes/)
```
