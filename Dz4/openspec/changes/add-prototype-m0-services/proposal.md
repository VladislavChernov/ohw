# Proposal: Веха 0 — инфраструктура прототипа (Config/Glossary Service + compose)

> Корневой change ДЗ4: с нуля поднимается исполняемый контур прототипа.
> Всё автономно — общий `D:\Otus\infra` / shared ollama / `ohw_kit` не используются.

## Зачем

Прототип документирован (ADR-001…020, `docs/prototype_requirements.md §1–4`), но кода нет:
в `prototype/` только compose-каркас, конфиги, domain_profiles и eval. Чтобы запустить
первый работающий срез платформы, нужна основа: два сервиса конфигурации/глоссария,
локальный Neo4j, и сеть/профили Docker, чтобы дальше наращивать вехи M1–M4.

Это «веха 0» по `docs/prototype_requirements.md §6`: инфраструктура и базовые сервисы,
без которых нельзя тестировать pipeline/retriever.

## BR (бизнес-требования)

- **BR-1. Автономность.** Прототип запускается без внешних зависимостей: собственная
  docker-сеть `ohw_net`, собственные volumes (включая веса моделей), без shared ollama.
- **BR-2. Config Service (:8001).** Хранит Domain Profile (YAML) + namespace-конфиги,
  умеет валидировать/активировать профиль (endpoints `docs/04` §2, ADR-018).
- **BR-3. Glossary Service (:8003).** Загружает `glossary.{profile}.yaml` из
  `prototype/domain_profiles/`, выполняет RESOLVE/VALIDATE тегов (ADR-018).
- **BR-4. Neo4j (профиль `graph`).** Запускается локально с JVM-лимитами
  (max heap 1G, pagecache 512M), сеть `ohw_net`.
- **BR-5. Compose-профили.** `config` = Config + Glossary (всегда); `graph` = Neo4j;
  `topology` — задел под ADR-019 (эта веха — только каркас :8005, запуск по требованию).
- **BR-6. Каркас кода.** src-проект (uv/pyproject), первичны модули Config/Glossary,
  toolchain: pytest/ruff/mypy (запуск в dev-container или на ВМ).

## Что делаем

- `prototype/src/` : пакет `graphrag_proto` (src-layout), `pyproject.toml` (uv),
  два FastAPI-приложения: `config_service` (:8001) и `glossary_service` (:8003).
- SQLite для Config (хранит активный профиль + namespace, дефолты из
  `prototype/infra/config/namespaces.yaml`), Glossary — стек proto: SQLite.
- Загрузка Domain Profile YAML с валидацией (`POST /api/v1/config/domain/validate`),
  активация (`POST /api/v1/config/domain/activate`), Runtime API по `docs/04` §2.
- Glossary: `GET /api/v1/glossary/{domain}`, `RESOLVE`, `VALIDATE` (ADR-018, без домена
  в пути); активный домен Glossary получает pull-запросом у Config (`GET
  /api/v1/config/domain/active`, fallback `it`).
- `prototype/infra/compose.yaml`: образы двух сервисов + Neo4j, сети/volumes/
  профили `config`, `graph`, `topology` (каркас :8005).
- Toolchain + мини-макет dev-container (Python, uv, docker-outside-of-docker),
  чтобы `uv run pytest/ruff/mypy` выполнялись в VS Code/ВМ.

## Не делаем в этой вехе

- Ingestion/Query/Retriever (M1–M3): не входят в M0.
- Ollama-сервис и embeddings (M1/M3): веса и GPU-проверка — отдельная веха.
- Topology :8005 — только каркас (регистрация сервиса в compose/profile `topology`),
  без runtime-переключения.
- `POST /api/v1/config/domain/profile` (загрузка нового профиля) — фаза конфигуратора (M5+);
  в M0 профили читаются с read-only volume.

## Проверка (критерии приёмки M0)

- `docker compose -f prototype/infra/compose.yaml --profile config --profile graph up -d`
  стартует без ошибок; контейнеры `config-service`, `glossary-service`, Neo4j — healthy.
- Config Service отдаёт `GET /api/v1/config/domain/profiles` → список из
  `prototype/domain_profiles/domain_profile.{it,library,cinema}.yaml`.
- `POST /api/v1/config/domain/validate` и `activate` валиден на `it`.
- Glossary Service отдаёт `GET /api/v1/glossary/it` и корректно RESOLVE тег из
  `glossary.it.yaml`.
- В рабочей среде (dev-container/ВМ): `uv run pytest -q`, `uv run ruff check`,
  `uv run mypy` — зелёные.