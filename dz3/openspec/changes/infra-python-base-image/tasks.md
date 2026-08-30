# tasks.md — infra-python-base-image

## infra
- [x] `infra/python/Dockerfile` — базовый образ `ohw-python:3.13` (slim + uv + юзер `app`)
- [x] `infra/python/build.ps1` — скрипт сборки образа
- [x] `infra/README.md` — раздел «Базовый образ python», каталог компонентов

## dz3/simple
- [x] `simple/Dockerfile` — обе стадии `FROM ohw-python:3.13`, без `pip install uv` и без `useradd`
- [x] `simple/README.md` — prerequisite: сборка базового образа через `infra/python/build.ps1`

## Verify
- [x] `docker build` образа `ohw-python:3.13` проходит
- [x] `docker compose build app` в `simple/` проходит на новом базовом образе
- [x] `pytest` в `simple/` зелёный (8+2 теста, без живой Ollama)
