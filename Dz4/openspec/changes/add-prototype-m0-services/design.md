# Design: add-prototype-m0-services

## Решения

### D1. Пакет и структура

- src-layout, пакет `graphrag_proto` в `prototype/src/graphrag_proto/` (uv/uv_build).
- Два FastAPI-приложения в одном пакете, раздельные entry-points:
  - `graphrag_proto.config_service.app:create_app` → `config-service` (:8001);
  - `graphrag_proto.glossary_service.app:create_app` → `glossary-service` (:8003).
- Это позволяет в M0–M4 держать один src-проект, а сервисы выносить позже без
  раздувания структуры (компромисс скорости, согласован с ADR-020).

### D2. Config Service

- Загрузка дефолтов namespace из `prototype/infra/config/namespaces.yaml` (SSOT `docs/04` §5).
- Domain Profile: каталог `prototype/domain_profiles/`, файл `domain_profile.{domain}.yaml`;
  активный профиль в SQLite (`namespace: domain.active_profile`, дефолт по namespaces.yaml
  через `config_service/namespaces.py`).
- Эндпоинты (по `docs/04` §2 + `api_reference.md`):
  `GET /api/v1/config/domain/active|profiles|profile/{name}`,
  `POST /api/v1/config/domain/validate|activate`.
  `POST /api/v1/config/domain/profile` (загрузка нового профиля) — в скоуп M0 НЕ входит:
  профили читаются с read-only volume; реализуется на фазе конфигуратора (M5+).
- Коды ошибок: `400` — невалидный YAML/не-маппинг в `validate`; `422` — структурные
  ошибки при загрузке/активации и конфликт имён при активации; сам `validate` при
  невалидной структуре отдаёт `200` с `{valid, errors}` (проверочный эндпоинт).
- Активация профиля → пишем активный в SQLite (pull-модель: Glossary сам запрашивает
  `GET /api/v1/config/domain/active`; событий/уведомлений нет).

### D3. Glossary Service

- Каталог `prototype/domain_profiles/glossary.{domain}.yaml` (стек proto: SQLite).
- Endpoints (ADR-018, без домена в пути):
  - `GET /api/v1/glossary/{domain}` — словарь;
  - `POST /api/v1/glossary/resolve` — тело `{term, domain?}` →
    `{canonical_name, variants}`;
  - `POST /api/v1/glossary/validate` — тело `{domain?}` → `{valid, duplicates}`.
- Glossary связывается с Config (активный профиль) через HTTP к :8001
  (`GET /api/v1/config/domain/active`), без жёсткой связи: при недоступности Config
  или отсутствии `domain` в запросе используется fallback `it` (env `CONFIG_URL`).

### D4. Композ и сети

- Сеть `ohw_net` — **собственная** (bridge), НЕ external (в отличие от Dz3: там была
  `external: true` общая сеть `ohw_net` из `D:\Otus\infra`). В Dz4 сеть и volumes свои.
- Образы сервисов — build из `prototype/` (`dockerfile: Dockerfile`), деплой внутри сети.
- Профили: `config` (всегда), `graph` (Neo4j), `topology` (только каркас :8005, старт по запросу).
- Neo4j с лимитами JVM (max heap 1G, pagecache 512M) — ADR-001.

### D5. Dev-окружение

- Локально Python нет: toolchain — через dev-container (VS Code: python:3.13-bookworm
  + features `uv` + `docker-outside-of-docker`) или на ВМ (`uv sync --dev`).
- README в `prototype/`: быстрый старт для обоих сценариев.

## Модули

| Модуль | Изменение |
|---|---|
| `prototype/pyproject.toml` (новый) | src-layout, uv_build, deps (fastapi, uvicorn, pyyaml, sqlite), dev (pytest, ruff, mypy, pytest-mock), scripts: `graphrag-config`, `graphrag-glossary` |
| `prototype/src/graphrag_proto/config_service/*` | app, domain (YAML-модель), store SQLite, api |
| `prototype/src/graphrag_proto/glossary_service/*` | app, glossary loader/RESOLVE/VALIDATE, store SQLite |
| `prototype/infra/compose.yaml` | сервисы config/glossary/neo4j/топология, сети, volumes, профили, healthcheck'и |
| `prototype/Dockerfile` (новый) | python:3.11-slim, uv, entrypoints |
| `prototype/tests/*` | юнит: config (validate/activate), glossary (resolve/validate) |

## Open questions

- OQ1. Авторизация M0: для локального prototype допустимы `X-API-Key` или открытый
  доступ? Требование `security.md` §1 — X-API-Key; в M0 заглушка ключей из env
  (без центрального менеджмента). Решить в ревью.
- OQ2. Формат хранения glossary в SQLite vs прямой парсинг YAML: на старте допускаем
  прямой парс YAML (proto «стек proto: SQLite» = схема прототипа, миграция позже).