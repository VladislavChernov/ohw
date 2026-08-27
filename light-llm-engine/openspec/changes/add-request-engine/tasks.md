# Tasks: add-request-engine

## 1. Каркас проекта

- [x] `LICENSE` (MIT) и готовый конфиг `light-llm-engine.toml`.
- [x] `pyproject.toml` (uv, build-backend `uv_build`, entry point
      `light-engine = "llm_engine.cli:main"`), `README.md`, `.gitignore`.
- [x] Пакет `src/llm_engine` с модулями и `__main__.py`.

## 2. Модуль клиента ollama

- [x] `ollama_client.py`: `OllamaClient` + ошибки `OllamaConnectionError`,
      `OllamaResponseError`.
- [x] Unit-тесты: успешный `chat`, HTTP-ошибка, битый JSON, сетевой сбой,
      `DEFAULT_BASE_URL`.

## 3. Конфигурация

- [x] `config.py`: `Config` (dataclass), загрузка TOML через `tomllib`,
      чтение env `OLLAMA_*`, валидация диапазонов.
- [x] Unit-тесты: дефолты, приоритеты слоёв, отсутствующий файл — не ошибка,
      битый TOML — `ConfigError`.

## 4. Чтение входных файлов

- [x] `input_doc.py`: `load_docs(input_dir)` — только `.md`/`.txt`,
      коллизии имён → `InputError`, пустая/отсутствующая папка → `InputError`.
- [x] Unit-тесты: пустая папка, коллизия, пропуск чужих расширений,
      именование выходных файлов.

## 5. Рендер ответа

- [x] `renderer.py`: `render(text, source_name)` — markdown-документ,
      обрамляющий ответ модели заголовком с именем исходного файла.
- [x] Unit-тесты: структура выходного документа.

## 6. CLI и оркестрация

- [x] `cli.py`: `main(argv) -> int`, обработка всех ошибок с exit codes
      0/2/3/4, ретраи при пустом ответе.
- [x] Unit-тесты: сценарии для каждого exit code, перезапись выходных файлов.

## 7. Docker

- [x] `Dockerfile` (мульти-стейдж, непривилегированный `app`).
- [x] `compose.yaml` (сервисы `ollama` + `app`, volume, healthcheck),
      `compose.gpu.yaml`, `.env`, `.dockerignore`.
- [x] Проверка: `docker compose up --build` обрабатывает пример из `input/`.
      E2E пройден: ollama 0.32.15 + `qwen2.5:0.5b`, файл `input/smoke.txt`
      → `output/smoke.md`, `app-1 exited with code 0`. Негативные смоук-тесты
      образа: без модели → exit 2, недоступный ollama → exit 3.

## 8. Dev Container

- [x] `.devcontainer/` (Python 3.13, uv, доступ к Docker).
- [ ] Проверка: «Reopen in Container» + `uv sync --dev`.

## 9. README и финальная проверка

- [x] README: локальный запуск, Docker Compose, переменные окружения.
- [x] `uv run pytest -q -m "not integration"` (26 passed), `uv run ruff check .`,
      `uv run mypy src tests` — все зелёные.