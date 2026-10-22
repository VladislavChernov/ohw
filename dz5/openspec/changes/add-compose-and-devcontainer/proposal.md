# Proposal: add-compose-and-devcontainer (финальный стенд)

## Why

Compose собирался инкрементально: комментарии и env отстают от кода, healthcheck
`agent-fixed` использует `wget` в `scratch`-образе, а `wall` — `wget` в
`python:3.12-slim`, где его нет. Нет `.devcontainer`, `infra.yaml` и единого
README для запуска. Нужен финальный, проверяемый инфра-слой.

## What changes

- Финальный `infra/compose.yaml`: все сервисы, профили, healthchecks,
  зависимости, лимиты, volumes, сеть.
- Исправление healthcheck:
  - `agent` (alpine) — `wget`;
  - `agent-fixed` — runtime с shell или отдельный healthcheck-бинарник;
  - `wall` — Python healthcheck или `curl` в образе.
- `infra/.env.example` с документированными переменными.
- `.devcontainer/devcontainer.json` + Dockerfile: Go, Python, Docker CLI,
  проброс docker socket (Docker Desktop).
- `infra/infra.yaml` — машиночитаемая карта сервисов/портов/health.
- README: запуск, профили, пресеты, отчёты, лицензии, ограничения.
- Удаление устаревших комментариев/портов (`wall:5000`, `drujensen`).

## Non-goals

- Изменение бизнес-логики агента/стены (отдельные бандлы).
- Kubernetes/CI/CD.
- Публикация стенда в интернет.

## Risks

- `scratch` + healthcheck несовместимы; выбрать runtime с shell (alpine) или
  healthcheck-бинарник. Это влияет на размер образа, но не на безопасность.
- GPU-резервация ollama может не работать на машинах без NVIDIA; предусмотреть
  CPU-профиль/документацию.
- Docker socket в devcontainer — риск; только локальная разработка.
- Пароли/ключи в `.env` не коммитить; `.env.example` без секретов.

## Definition of done

- `docker compose config` валиден.
- `docker compose up -d --build` поднимает стенд; все healthchecks зелёные.
- `agent-fixed` healthcheck работает не только на бумаге.
- `wall` healthcheck работает в реальном образе.
- `.devcontainer` собирается и даёт `go`, `python`, `docker`.
- README соответствует compose и коду.
