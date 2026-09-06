# Задачи: M0 (add-prototype-m0-services)

> Toolchain выполняется в dev-контейнере `ohw-python:3.13` (Docker) или на ВМ.
> `[x]` проставлен только после зелёной проверки (`uv run pytest/ruff/mypy`).

## 1. Каркас проекта и окружение

- [x] 1.1. `prototype/pyproject.toml`: src-layout, uv_build, deps (fastapi, uvicorn, pyyaml),
      dev-group (pytest, ruff, mypy, pytest-mock, httpx2, types-PyYAML),
      scripts `graphrag-config`/`graphrag-glossary`.
- [x] 1.2. `prototype/src/` пакет `graphrag_proto` + `config_service`/`glossary_service`.
- [x] 1.3. `prototype/Dockerfile` (python:3.13-slim, uv, `uv sync --no-dev --frozen`
      с `uv.lock`, entrypoint `uv run --no-sync`) + `.dockerignore`.
- [x] 1.4. README `prototype/`: быстрый старт через `ohw-python:3.13` (docker volume) и через ВМ.
- [x] 1.5. dev-контейнер: вместо `.devcontainer/` используем общий базовый образ
      `ohw-python:3.13` (python 3.13 + uv), код — volume `D:\Otus\ohw\Dz4\prototype:/app`.

## 2. Config Service (:8001)

- [x] 2.1. Модель Domain Profile: загрузка YAML с валидацией обязательных секций
      (`profile`, `ontology`, `extraction`, `validation`, `canonicalization`,
      `chunking`, `context_assembly`).
- [x] 2.2. SQLite-store (`runtime_config` key/value, `check_same_thread=False`);
      активный профиль хранится в namespace `domain.active_profile`; каталог БД
      создаётся автоматически (работает и с `CONFIG_DB_PATH`, и с дефолтом).
- [x] 2.3. Runtime API: `GET /api/v1/config/domain/active|profiles|profile/{name}`
      (guard от path-траверса в `profile/{name}`).
- [x] 2.4. `POST /api/v1/config/domain/validate|activate`; коды ошибок:
      `400` — битый/не-маппинговый YAML; `validate` при невалидной структуре —
      `200` + `{valid, errors}`; `422` — конфликт `profile.name` vs `domain` при активации
      и структурные ошибки при загрузке профиля.
- [x] 2.5. Оповещение Glossary при активации — **pull-модель**: Config только сохраняет
      активный профиль, Glossary сам запрашивает `GET /api/v1/config/domain/active`
      (см. 3.5). Событий/уведомлений нет.
- [x] 2.6. Дефолты из `prototype/infra/config/namespaces.yaml` (модуль `namespaces.py`):
      `domain.active_profile` задаёт активный профиль по умолчанию (fallback `it`).
- [x] 2.7. Юнит-тесты: валидация, активация, персист через пересоздание app,
      дефолты из namespaces, 400/422, 404 на неизвестный домен (pytest).

## 3. Glossary Service (:8003)

- [x] 3.1. Загрузка `glossary.{domain}.yaml` из `domain_profiles/` (guard от path-траверса).
- [x] 3.2. `GET /api/v1/glossary/{domain}` — возвращает словарь.
- [x] 3.3. `POST /api/v1/glossary/resolve` — тело `{term, domain?}` →
      `{canonical_name, variants}` (точное + case-insensitive + function_synonyms).
- [x] 3.4. `POST /api/v1/glossary/validate` — тело `{domain?}` → `{valid, duplicates}`.
- [x] 3.5. Активный домен через Config :8001 (env `CONFIG_URL`) — pull при отсутствии
      `domain` в запросе; при недоступности Config — fallback `it`.
- [x] 3.6. Юнит-тесты: resolve/validate на sample `glossary.it.yaml`, variants,
      дубликаты, 404 на малфомед YAML/неизвестный домен.

## 4. Compose и деплой

- [x] 4.1. `prototype/infra/compose.yaml`: сервисы `config-service`, `glossary-service`
      (build из `prototype/` через общий Dockerfile), Neo4j (JVM-лимиты 1G/512M),
      сеть `ohw_net` (bridge, своя).
- [x] 4.2. Профили: `config` (всегда), `graph` (Neo4j), `topology` (каркас :8005, по запросу).
- [x] 4.3. Volumes: `neo4j_data`, `config_data`; `models_data`/`ollama_data` — задел
      под фазу embeddings/llm.
- [x] 4.4. `docker compose config --quiet` — валиден.
- [x] 4.5. Healthcheck'и: `config-service` и `glossary-service` (GET на свои эндпоинты
      через `python -c urllib`), Neo4j (`cypher-shell RETURN 1`).
- [x] 4.6. Smoke: поднят `config`+`graph`, `GET profiles` → 3 домена, `activate it` → 200,
      `resolve Big-O` → `big_o`, Neo4j `Started.` (7474/7687).

## 5. Верификация

- [x] 5.1. `uv run pytest -q` — 26 passed.
- [x] 5.2. `uv run ruff check` — чисто.
- [x] 5.3. `uv run mypy` — Success (9 source files, strict).
- [x] 5.4. Критерии приёмки из `proposal.md` выполнены (включая 2.5/3.5 — pull-модель).

## Открытые вопросы (вне M0)

- OQ1. Авторизация: `AUTH_API_KEY` задан в compose, но в коде пока не проверяется
  (локальный prototype, заглушка). Центральный менеджмент ключей — M1.
- OQ2. Glossary читает YAML напрямую (прото-стек); SQLite-схема словаря — по мере
  наполнения.
- OQ3. `POST /api/v1/config/domain/profile` (загрузка нового профиля) реализуется на
  фазе конфигуратора (M5+); в M0 профили читаются с read-only volume.