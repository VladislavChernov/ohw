# GraphRAG prototype (ДЗ4)

Прототип доменно-агностичной GraphRAG-платформы. Реализовано на текущую веху:
Config Service (:8001), Glossary Service (:8003), Neo4j (профиль `graph`),
Ingestion API (:8002, пайплайн 9 этапов + реальный COMMIT), Query API (:8000,
асинхронный контур: очередь → воркер → SSE, ADR-023) и браузерный демо-контур
UI (:8503).

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

## Демо-контур (:8503) и сквозной e2e

Браузерный прогон «загрузить документ → запрос → ответ со `sources`» — в
`docs/demo_runbook.md` (в `Dz4/docs`). Коротко:

```bash
# полный стек: config + graph + ingestion + llm (в llm-профиле: valkey, query-api,
# query-worker, llama.cpp, demo-ui); llama.cpp требует локальный GGUF-файл
docker compose --profile config --profile graph --profile ingestion --profile llm up -d --wait

# UI: http://localhost:8503
# автоматизированный сквозной прогон (сбрасывает volume'ы — предусловие детерминизма)
bash infra/scripts/run_demo_e2e.sh
```

Тесты:
- юнит/контрактные — `uv run pytest -q` (e2e-модуль исключён `addopts = "-m 'not e2e'"`);
- e2e по живому стеку — `uv run pytest -m e2e` с адресами `--e2e-ingest/--e2e-query/--e2e-key`
  либо env `E2E_INGEST/E2E_QUERY/E2E_KEY`.

## Структура

```
src/graphrag_proto/
├── config_service/    # Config Service (:8001): domain profiles, SQLite, дефолты namespace
├── glossary_service/  # Glossary Service (:8003): словарь, RESOLVE/VALIDATE
├── ingestion_service/ # Ingestion API (:8002): пайплайн 9 этапов, реальный COMMIT (Neo4j)
├── query_service/     # Query API (:8000): очередь + воркер + SSE-стрим (ADR-023)
├── retrieval/         # Retrieval: адаптеры и вытеснение контекста по score
├── demo_ui/           # Streamlit-демо (:8503): client.py (транспорт), app.py, cli.py
tests/                 # pytest (юнит + opt-in e2e, marker `e2e`)
infra/                 # compose.yaml, scripts/run_demo_e2e.sh, samples/
domain_profiles/       # domain_profile.{domain}.yaml + glossary.{domain}.yaml
```