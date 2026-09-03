# Design: запуск simple и advanced через общий `/infra`

## Сеть и компоненты

- Shared `ohw-ollama` — **один контейнер** из каталога `infra/` (локальный
  `D:\Otus\infra\compose.yaml`, `profiles:["ollama"]`), на общей bridge-сети
  **`ohw_net`**, порт `11434`, volume `ohw_ollama_models`. Поднимается через
  `D:\Otus\infra\up.ps1 -Project <папка>` по объявлению `infra.yaml`.
- `ohw_net` — обычный bridge (`internal=false`): у app-контейнеров есть исходящий
  интернет (проверено: `urllib` из `ohw-python` на `ohw_net` → 200 от
  `jsonplaceholder.typicode.com`). Это нужно E2E-прогонам против внешнего API.

## Build-контекст и kit-зависимость

`advanced` зависит от `ohw-kit @ file:../../kit` (из `dz3/advanced` → `ohw/kit`).
При сборке в docker `uv sync` резолвит этот относительный путь **из файловой
системы build-контекста** (не из интернета). Поэтому build-контекст advanced
обязан включать и `dz3/advanced`, и `kit/` → **контекст = корень монорепо `ohw/`**,
внутри Dockerfile `WORKDIR /app/dz3/advanced`, `../../kit` → `/app/kit`.

```yaml
# dz3/compose.yaml
services:
  app:
    build:
      context: ../..            # корень монорепо ohw/ (есть и kit/, и dz3/advanced/)
      dockerfile: dz3/Dockerfile
    command: ["json_testgen_advanced"]   # python -m json_testgen_advanced
    environment:
      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://ohw-ollama:11434}
      OLLAMA_MODEL: ${OLLAMA_MODEL:-qwen2.5:7b-instruct}
    volumes:
      - ../advanced/input:/data/input
      - ../advanced/output:/data/output
    networks:
      - ohw-net

networks:
  ohw-net:
    external: true
    name: ohw_net
```

## Общий Dockerfile (корень dz3) + `python -m`

`simple` жёстко зашивает ENTRYPOINT. Для общего Dockerfile модуль задаётся через
compose `command` (аргумент к `python -m`):

```dockerfile
FROM ohw-python:3.13 AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/dz3/advanced
COPY dz3/advanced/README.md dz3/advanced/pyproject.toml dz3/advanced/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev
COPY dz3/advanced/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM ohw-python:3.13
WORKDIR /data
COPY --from=builder /app/dz3/advanced/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
ENTRYPOINT ["python", "-m"]
```

`uv sync --frozen` внутри соберёт и `ohw-kit` из `/app/kit` (относительный путь
резолвится от `pyproject.toml`).

## Почему простой `uv run` на хосте — не путь

Каталог `/infra` кодифицирует запуск через контейнеры (общий ollama + app на
`ohw_net`). Хост-запуск (`uv run ...` против `localhost:11434`) обходит этот
контракт и не воспроизводится на других машинах — поэтому E2E идёт только через
`compose`.

## Что НЕ меняем

- simple — замороженную обвязку не трогаем (своя `Dockerfile`/`compose.yaml`/`infra.yaml`).
- dz1/dz2/light-llm-engine — не касаемся.
- JSON-контракт v3 и ядро advanced — без изменений.
