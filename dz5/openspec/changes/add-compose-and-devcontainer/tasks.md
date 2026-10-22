# Tasks: add-compose-and-devcontainer

## 1. Аудит текущего compose

- [x] Зафиксировать устаревшие комментарии/порты (`drujensen`, `wall:5000`).
- [x] Зафиксировать битые healthcheck: `wget` в `scratch` (`agent-fixed`),
      `wget` в `python:3.12-slim` (`wall`).
- [x] Определить целевую карту сервисов и профилей.

## 2. Compose

- [ ] `infra/compose.yaml`: сервисы/порты/профили по спеке.
- [x] `wall`: порт `8000`, healthcheck `/health-check`, без `depends_on: ollama`.
- [x] `agent`: env Guardrails (`GUARDRAILS_BASE_URL`, guard names, timeout).
- [ ] `agent-fixed`: исправить healthcheck (alpine runtime или бинарник);
      сейчас также падает сборка на `HandleStatus`/`HandleUI`.
- [ ] `attack-runner`: профиль `attack`, volume `reports/`, зависимости.
- [x] L3 limits и restart-policy (wall `2048M`, agent/ollama лимиты, restart).
- [x] Убрать `WALL_BASE_URL`/`5000`, обновить комментарии.
- [ ] `infra/.env.example`; `.env` в `.gitignore`.
- [x] `docker compose config` зелёный.

## 3. Healthchecks

- [x] `ollama` — модель загружена (контейнер `healthy`).
- [x] `agent` — `/healthz` (контейнер `healthy`).
- [ ] `agent-fixed` — проверить в реальном runtime, не на бумаге.
- [x] `wall` — Python/urllib, без внешнего `curl`/`wget` (контейнер `healthy`).
- [ ] Start periods достаточны для первой загрузки модели.
- [ ] Healthcheck не требует секретов.

## 4. Devcontainer

- [ ] `.devcontainer/devcontainer.json` + Dockerfile.
- [ ] Go 1.26, Python 3.12, Docker CLI.
- [ ] Docker socket / DinD для Docker Desktop.
- [ ] postCreate: зависимости.
- [ ] `.devcontainer` собирается; `go version`, `python --version`,
      `docker version` работают.

## 5. `infra/infra.yaml` и README

- [ ] `infra/infra.yaml`: сервисы, порты, health, presets.
- [ ] README: обзор, пресеты, запуск, профили, отчёты, лицензии, ограничения.
- [ ] Убрать устаревшие порты/имена.
- [ ] Ссылки на demo-скрипты и `reports/latest.md`.

## 6. Проверка

- [ ] `docker compose build` — все образы (agent/wall/db/ollama зелёные;
      agent-fixed падает).
- [ ] `docker compose up -d` — все healthy (сейчас только
      `ollama` + `agent` + `wall`).
- [x] `db-seed` и smoke `/chat` (users=5, preset 1 ответил 200).
- [ ] `attack-runner` smoke (профиль `attack`).
- [ ] `docker compose down` без удаления volumes.
- [ ] Повторный `up` не ломает состояние.

## 7. Завершение

- [ ] Все проверки зелёные; галочки по факту.
- [ ] Коммит только `dz5/` после явной просьбы.
