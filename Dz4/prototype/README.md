# Гибридная RAG — прототип (ДЗ4)

Учебный прототип проекта «Гибридная RAG». Реализованы:
Config Service (:8001), Glossary Service (:8003), Neo4j (профиль `graph`),
Ingestion API (:8002, пайплайн 9 этапов + реальный COMMIT), Query API (:8000,
асинхронный контур: очередь → воркер → SSE, ADR-023) и браузерный демо-контур
UI (:8503).

## Окружение

Для разработки нужны Python 3.11+ и uv либо dev-контейнер `ohw-python:3.13`
(Python 3.13 + uv; образ собирается через [общую инфраструктуру](../../infra/README.md)).
Пути ниже — для этой копии репозитория; при переносе замените их.
Linux-окружение в отдельном volume не смешивается с `.venv` хоста.

```powershell
docker run --rm -v 'd:\Otus\ohw\Dz4\prototype:/app' -v dz4_dev_venv:/app/.venv -w /app ohw-python:3.13 uv sync --dev --frozen
docker run --rm -v 'd:\Otus\ohw\Dz4\prototype:/app' -v dz4_dev_venv:/app/.venv -w /app ohw-python:3.13 uv run --no-sync pytest -q
docker run --rm -v 'd:\Otus\ohw\Dz4\prototype:/app' -v dz4_dev_venv:/app/.venv -w /app ohw-python:3.13 uv run --no-sync ruff check
docker run --rm -v 'd:\Otus\ohw\Dz4\prototype:/app' -v dz4_dev_venv:/app/.venv -w /app ohw-python:3.13 uv run --no-sync mypy
```

Либо Bash в WSL с установленными Python и uv:

```bash
cd /mnt/d/Otus/ohw/Dz4/prototype
uv sync --dev --frozen
uv run --no-sync pytest -q
uv run --no-sync ruff check
uv run --no-sync mypy
```

Compose задаёт runtime-пути через окружение; при отдельном запуске сервисов
значения по умолчанию зависят от CWD. Для типизации предпочтителен Python 3.13
из dev-образа (конфигурация mypy учитывает современные stubs).

## Запуск демо (PowerShell)

У `ingestion-api` нет build-секции, образ собирается отдельно.

```powershell
docker build --file 'd:\Otus\ohw\Dz4\prototype\Dockerfile' --tag ohw/ingestion-service:prototype 'd:\Otus\ohw\Dz4\prototype'
docker compose --file 'd:\Otus\ohw\Dz4\prototype\infra\compose.yaml' --profile config --profile graph --profile ingestion --profile llm up -d --build --wait
```

UI — http://localhost:8503. Требования, порты, остановка и безопасность —
в [основном README](../README.md). Технические имена `graphrag_proto`,
CLI `graphrag-*` и образы сохранены.

Есть Topology Orchestrator (:8005), embeddings (:8004), reranker (:8006)
и расширяемые чанкеры. Основной Compose использует `EMBEDDER=deterministic`
и `RERANKER=noop`; запуск дополнительных профилей не переключает эти адаптеры.
Это проверка интеграции, не оценка качества семантического поиска.

## Модель и сквозной e2e

llama.cpp — self-contained: GGUF загружается при сборке из Hugging Face
с закреплённой ревизией и проверкой SHA-256, локальный файл модели не требуется.
Сценарий браузера — [user guide](../docs/demo_user_guide.md),
операционные детали — [runbook](../docs/demo_runbook.md).
Команды запуска оттуда сверяйте с явным Compose-файлом выше.

Обычный pytest исключает e2e через `addopts = "-m 'not e2e'"`.
Прямой запуск ниже требует уже поднятого отдельного тестового стека и чистого
набора данных: посторонние документы могут вытеснить тестовый источник из top-k.

```bash
# Bash в WSL, Python и uv установлены, uv sync выполнен
cd /mnt/d/Otus/ohw/Dz4/prototype
uv run --no-sync pytest -m e2e --e2e-ingest=http://localhost:8002 --e2e-query=http://localhost:8000 --e2e-key="${GRAPH_AUTH_API_KEY:-changeme}" -v
# Только сбор тестов, без обращения к сервисам
uv run --no-sync pytest --collect-only -m e2e /mnt/d/Otus/ohw/Dz4/prototype/tests/test_demo_e2e.py -q
uv run --no-sync python /mnt/d/Otus/ohw/Dz4/prototype/infra/eval/run_eval.py --help
```

Прямой pytest не поднимает стек автоматически. Скрипт
[run_demo_e2e.sh](./infra/scripts/run_demo_e2e.sh) пока не рекомендуется:
он вычисляет вложенный путь `prototype/prototype`, а Compose вызывается
без `--file`. По замыслу скрипт удаляет volumes перед прогоном;
его `--down` тоже удаляет volumes. При обновлении README скрипт не менялся.

## Структура

Основные каталоги и файлы (сокращённая схема):

```text
d:\Otus\ohw\Dz4\prototype\
├── src/graphrag_proto/
│   ├── config_service/      # Domain Profiles, SQLite, настройки
│   ├── glossary_service/    # Глоссарии и канонические имена
│   ├── ingestion_service/   # Девять этапов, COMMIT в Neo4j, чанкеры
│   ├── query_service/       # Очередь, worker, SSE
│   ├── topology_service/    # Карта адаптеров и runtime overrides
│   ├── embeddings_service/  # BGE-M3
│   ├── reranker_service/    # BGE reranker
│   ├── retrieval/           # Адаптеры, поиск, слияние контекста
│   ├── demo_ui/             # Streamlit
│   └── plugin_registry.py   # Реестр плагинов
├── tests/                   # Юнит/контрактные тесты и opt-in e2e
├── infra/                   # Compose, конфигурация, скрипты и eval
├── domain_profiles/         # Domain Profiles и глоссарии
└── infra_topology.yaml      # Базовая карта адаптеров
```