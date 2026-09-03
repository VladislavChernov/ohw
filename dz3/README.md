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

Варианты запускаются **раздельно** — по одному стартовому скрипту на вариант.
Скрипт сам: соберёт базовый образ `ohw-python:3.13` (если его ещё нет),
поднимет общий ollama (`infra/up.sh`) и запустит нужный compose:

```bash
# advanced: отчёт в advanced/output/report.md, план в plan.json
cd ohw/dz3 && ./start_advanced.sh          # CPU; ./start_advanced.sh --gpu для CUDA

# simple (отдельно):
cd ohw/dz3 && ./start_simple.sh            # CPU; --gpu аналогично
```

Ручной эквивалент (если хочется по шагам):

```bash
# 0) базовый образ ohw-python:3.13 (один раз, собирается локально)
cd ohw && ./infra/python/build.sh

# 1) shared ollama на сети ohw_net (общий для обоих вариантов)
cd ohw/infra && ./up.sh ../dz3

# 2) advanced:
cd ohw/dz3 && docker compose up --build
# 2') simple (отдельно, свой compose):
cd ohw/dz3/simple && docker compose up --build
```

Генерация плана на CPU занимает несколько минут (`OLLAMA_TIMEOUT` по умолчанию
1200 с); для GPU запустите инфру с `./up.sh ../dz3 --gpu`.

## Нужен только один вариант? Минимальный набор

`advanced` жёстко зависит от двух соседних папок монорепо: `kit/` (path-зависимость
`ohw-kit = { path = "../../kit" }`) и `infra/` (общий ollama для docker-запуска).
Отдельно скопировать папку `dz3/advanced` нельзя — `uv sync` не найдёт kit.

Проще всего склонировать репозиторий целиком (это только тексты, несколько МБ).
Если хочется выкачивать лишь часть — **sparse-checkout внутри того же одного
репозитория** (никаких сторонних репозиториев не добавляется):

```bash
git clone --no-checkout https://github.com/VladislavChernov/ohw.git
cd ohw
git sparse-checkout set dz3/advanced kit infra   # только advanced (+ kit, infra)
# или: git sparse-checkout set dz3/simple        # только simple (kit не нужен)
git checkout
```

Если ollama уже стоит у вас локально (не через инфру), `infra` можно не выкачивать:
`git sparse-checkout set dz3/advanced kit` и запускать с `OLLAMA_BASE_URL`.
Дальше — как в «Способы запуска» (всё качается из стандартных реестров: пакеты
из PyPI через uv, образы из Docker Hub, модель — ollama registry; сторонних
git-репозиториев не требуется).

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
