# Tasks: add-ai-testcase-generator

## 1. Каркас проекта (uv)

- [x] 1.1 `uv init --package ai-testcase-generator` (Python 3.13), имя пакета `ai_testgen`
- [x] 1.2 Добавить зависимости: `uv add httpx pydantic`; dev: `uv add --dev pytest pytest-mock ruff mypy`
- [x] 1.3 Настроить `[project.scripts]` → `ai-testgen = "ai_testgen.cli:main"`
- [x] 1.4 README: как получить uv, как запускать локально и в контейнере

## 2. Входные данные и CLI

- [x] 2.1 `cli.py`: argparse (`--count/-n`, `--input-dir`, `--output-dir`,
      `--url`, `--max-retries`, `--temperature`), валидация значений, exit codes 2/3/4
- [x] 2.2 `input_doc.py`: скан входной папки (.md/.txt), детект коллизий имён
      (одинаковый basename, разные расширения → exit 2), пропуск чужих расширений
      с warning; чтение файлов, извлечение требований и URL (fallback на `--url`)
- [x] 2.3 Пакетный цикл: файл → выходной `<имя>.md` в `--output-dir`;
      fail-fast при ошибке любого файла
- [x] 2.4 Примеры `examples/input/{auth.md,cart.txt}` с требованиями к демо-сайту

## 3. Интеграция с ollama

- [x] 3.1 Конфиг из окружения: `OLLAMA_BASE_URL` (дефолт
      `http://host.docker.internal:11434`), `OLLAMA_MODEL`
- [x] 3.2 `ollama_client.py`: POST `/api/chat`, `format="json"`, таймаут,
      обработка connection errors → exit code 3
- [x] 3.3 `prompt.py`: системный промпт со схемой JSON, распределением
      positive/negative (~60/40), требованием шагов и ожидаемых результатов

## 4. Валидация и рендер

- [x] 4.1 Pydantic-модель `TestCase` (id, type, title, preconditions, steps, expected)
- [x] 4.2 `validator.py`: количество == N; есть positive и negative;
      непустые поля. Ошибки — человекочитаемым списком
- [x] 4.3 Retry-цикл в `cli.py`: повторный запрос с feedback об ошибке,
      лимит `--max-retries`; исчерпаны → exit code 4 без записи файла
- [x] 4.4 `renderer.py`: markdown — заголовок, сводка (сколько positive/negative),
      секции позитивных и негативных кейсов, у каждого кейса таблица шагов

## 5. Тесты

- [x] 5.1 Unit: сканер входной папки (несколько файлов; коллизия имён; пустая
      папка; чужие расширения; URL в файле / через `--url` / отсутствует)
- [x] 5.2 Unit: валидатор (успех; мало кейсов; нет негативных; битый JSON;
      обёртка `{"cases": [...]}`; одиночный объект)
- [x] 5.3 Unit: renderer — ровно N кейсов в выводе
- [x] 5.4 Unit: retry-логика с замоканным клиентом (вторая попытка успешна)
- [x] 5.5 Integration (маркер `@pytest.mark.integration`, требуют живого ollama):
      реальная генерация N=10 → файл содержит 10 кейсов, есть оба типа

## 6. Контейнеризация

- [x] 6.1 Мультистейдж Dockerfile: builder c `ghcr.io/astral-sh/uv` → runtime `python:3.13-slim`
- [x] 6.2 Точка входа `python -m ai_testgen`; рабочая точка монтирования `/data`
- [x] 6.3 Проверка: `docker run --rm -v ./input:/data/input -v ./output:/data/output ai-testgen -n 10`

## 7. Dev Container

- [x] 7.1 `.devcontainer/devcontainer.json`: python-образ + feature uv +
      feature docker-outside-of-docker, расширения (Python, Ruff, TOML)
- [x] 7.2 post-create: `uv sync --dev`; env `OLLAMA_BASE_URL` по умолчанию
- [x] 7.3 VS Code tasks/launch: запуск CLI, pytest (unit), pytest (integration)

## 8. Финализация

- [x] 8.1 `ruff check . && mypy . && pytest -m "not integration"` — зелёные
- [ ] 8.2 Прогон на реальном сайте из ДЗ, проверка отчёта глазами
- [ ] 8.3 Заархивировать change: перенести спеку в `openspec/specs/`
