# Change: базовый python-образ в infra (`ohw-python:3.13`) + переход simple на него

## Why

Dockerfile'ы проектов (dz2, light-llm-engine, dz3/simple) — идентичный шаблон:
`python:3.13-slim-bookworm` + `pip install uv` + создание unprivileged-юзера
`app`. Это общая инфраструктура, а не домашняя логика: дублируется в каждом
проекте, и обновление pip/uv требует правки N файлов. По конвенции каталога
`infra/` общие компоненты описываются один раз.

Важно: **dz1 и dz2 — замороженные памятники и не изменяются**; новый образ —
опциональная инфраструктура для *активных* проектов (сейчас — `dz3/simple`).

## What Changes

- **infra**: новый компонент-артефакт `python` — папка `infra/python/` с
  `Dockerfile` базового образа `ohw-python:3.13` (slim + uv + юзер `app`) и
  скриптом сборки `build.ps1`.
- **infra**: README — раздел «Базовый образ python» + обновление каталога
  компонентов.
- **dz3/simple**: Dockerfile наследуется от `ohw-python:3.13` (обе стадии),
  убраны `pip install uv` и создание юзера `app`; поведение рантайма не меняется.
- **dz3/simple**: README — упоминание prerequisite-сборки базового образа.

## Impact

- Affected: `infra/` (новые файлы + README), `Dz3/simple/Dockerfile`,
  `Dz3/simple/README.md`.
- dz1, dz2, light_llm_engine — без изменений.
- Прогоны (pytest) и контракт `compose.yaml` не меняются.
