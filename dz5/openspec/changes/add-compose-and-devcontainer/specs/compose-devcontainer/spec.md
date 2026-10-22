# Spec: compose-and-devcontainer

## ADDED Requirements

### Requirement: Сервисы

| Сервис | Порт | Профиль | Назначение |
|--------|------|---------|------------|
| `ollama` | `11434` | default | abliterated LLM, GPU |
| `db` | — | default | SQLite CLI/volume |
| `db-seed` | — | `tools` | schema + seed |
| `db-restore` | — | `tools` | восстановление |
| `db-query` | — | `tools` | ручной SELECT |
| `agent` | `8080` | default | пресеты 1–4 |
| `agent-fixed` | `8081` | default | preset 5 |
| `wall` | `8000` | default | Guardrails Server |
| `attack-runner` | — | `attack` | прогон атак |

- Сеть `ib-network`, volume `ollama_data`, `db_data` (+ `reports_data` при
  необходимости).
- `agent` не зависит от `wall` по health: при `EXTERNAL_GUARD=on` и
  недоступной стене он должен вернуть `503`, а не не запуститься.
- `wall` не зависит от `ollama`.
- `attack-runner` зависит от `agent`, `agent-fixed`, `wall` (healthy).

### Requirement: Healthchecks

| Сервис | Проверка |
|--------|----------|
| `ollama` | `ollama show "$OLLAMA_MODEL"` |
| `agent` | `wget -qO- http://localhost:8080/healthz` (alpine) |
| `agent-fixed` | runtime с shell/healthcheck-бинарником; **не** `wget` в `scratch` |
| `wall` | `python -c` urllib или `curl` (установить в образ) на `/health-check` |

- Интервалы: 10s, timeout 3s, retries 12–60, start_period с учётом загрузки
  модели.
- Healthcheck не должен требовать секретов.

### Requirement: Переменные

`infra/.env.example`:

```dotenv
OLLAMA_MODEL=huihui_ai/llama3.2-abliterate:3b
BUILTIN_GUARDS=off
EXTERNAL_GUARD=off
ALLOW_WRITES=off
ALLOW_RUNTIME_TOGGLES=off
GUARDRAILS_BASE_URL=http://wall:8000
INPUT_GUARD_NAME=input-defense
OUTPUT_GUARD_NAME=output-privacy
GUARDRAILS_TIMEOUT=10s
```

- Секретов в примере нет.
- `.env` в `.gitignore`.

### Requirement: Compose-гигиена

- `name: dz5-stand`.
- Все образы с pinned-тегами; `build` + `image`.
- `restart: unless-stopped` для долгоживущих.
- L3 limits: ollama `4000M`, agent/fixed `512M`, wall `1024M`.
- `depends_on` только где нужен порядок; без циклических зависимостей.
- Комментарии соответствуют коду: нет `drujensen`, `wall:5000`,
  «FastAPI proxy».
- `docker compose config` без ошибок.

### Requirement: Devcontainer

`.devcontainer/devcontainer.json`:

- base: Debian/Ubuntu с Go 1.26 и Python 3.12;
- features: `docker-in-docker` или проброс `/var/run/docker.sock` для Docker
  Desktop;
- postCreate: `go mod download`, `pip install -r ...` (по наличию);
- extensions: Go, Python, YAML, Docker;
- `remoteUser` non-root; рабочая папка `/workspaces/ohw`;
- GPU не обязателен; документировать, что ollama с GPU требует Docker
  Desktop/NVIDIA на хосте.

### Requirement: `infra/infra.yaml`

Машиночитаемая карта:

```yaml
project: dz5-stand
services:
  agent:
    port: 8080
    health: /healthz
    preset: "1-4"
  agent-fixed:
    port: 8081
    health: /healthz
    preset: 5
  wall:
    port: 8000
    health: /health-check
```

Используется demo-скриптами/README; не дублирует секреты.

### Requirement: README

- Обзор, матрица 5 пресетов, текущий статус (что измерено/не измерено).
- Быстрый старт: `docker compose ...`, seed, health.
- Профили `tools`/`attack`.
- Runtime toggles и ограничение `ALLOW_RUNTIME_TOGGLES`.
- Отчёты и `latest.md`.
- Лицензии: свой код MIT, Guardrails/Augustus Apache 2.0.
- Ограничения: локальный стенд, синтетические данные, не production.

## Definition of done

- [ ] `docker compose config` валиден.
- [ ] `docker compose up -d --build` — все healthchecks зелёные.
- [ ] `agent-fixed` healthcheck реально работает.
- [ ] `wall` healthcheck реально работает.
- [ ] `docker compose --profile tools run --rm db-seed` работает.
- [ ] `docker compose --profile attack run --rm attack-runner` работает.
- [ ] `.devcontainer` собирается; `go`, `python`, `docker` доступны.
- [ ] `infra.yaml` соответствует compose.
- [ ] README не содержит устаревших портов/имён.
