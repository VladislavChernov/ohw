# GraphRAG prototype (ДЗ4)

Прототип доменно-агностичной GraphRAG-платформы. Веха M0: Config Service (:8001),
Glossary Service (:8003), Neo4j (профиль `graph`).

## Окружение

Локально Python отсутствует — все команды выполняются в **dev-контейнере**
`ohw-python:3.13` (python 3.13 + uv), код подключается volume:

```powershell
# один раз: установка окружения (uv.lock)
docker run --rm -v "$(Get-Location):/app" -w /app ohw-python:3.13 uv sync

# тесты / линт / типизация (из этой папки после sync)
docker run --rm -v "$(Get-Location):/app" -w /app ohw-python:3.13 uv run pytest -q
docker run --rm -v "$(Get-Location):/app" -w /app ohw-python:3.13 uv run ruff check
docker run --rm -v "$(Get-Location):/app" -w /app ohw-python:3.13 uv run mypy
```

Либо на ВМ (`uv sync --dev && uv run pytest -q`).

Примечание: объёмы — не про Docker сначала. Пути по умолчанию считаются
относительно CWD (`domain_profiles/`, `infra/config/`), поэтому в рантайме
compose задаёт их env-переменными.

## Запуск сервисов (локально, без compose)

```powershell
docker run --rm -v "$(Get-Location):/app" -w /app -p 8001:8001 `
  -e DOMAIN_PROFILES_DIR=/app/domain_profiles `
  -e CONFIG_DB_PATH=/app/runtime/config.db `
  -e NAMESPACES_PATH=/app/infra/config/namespaces.yaml `
  ohw-python:3.13 uv run --no-sync graphrag-config

# Glossary Service (:8003); активный домен — pull из Config (CONFIG_URL),
# при недоступности — fallback "it"
docker run --rm -v "$(Get-Location):/app" -w /app -p 8003:8003 `
  -e DOMAIN_PROFILES_DIR=/app/domain_profiles -e CONFIG_URL=http://localhost:8001 `
  ohw-python:3.13 uv run --no-sync graphrag-glossary
```

## Структура

```
src/graphrag_proto/
├── config_service/    # Config Service (:8001): domain profile, SQLite, дефолты namespace
└── glossary_service/  # Glossary Service (:8003): словарь, RESOLVE/VALIDATE
tests/                 # pytest
infra/                 # compose.yaml, config/ (namespaces.yaml, adapters.yaml)
domain_profiles/       # domain_profile.{domain}.yaml + glossary.{domain}.yaml
```