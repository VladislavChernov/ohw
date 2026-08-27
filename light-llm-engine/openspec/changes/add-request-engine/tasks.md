# Tasks: add-request-engine

## 1. Каркас проекта

- [x] `LICENSE` (MIT) и готовый конфиг `light-llm-engine.toml`.
- [ ] `pyproject.toml` (uv, build-backend `uv_build`, entry point
      `llm-engine = "llm_engine.cli:main"`), `README.md`, `.gitignore`.
- [ ] Пакет `src/llm_engine` с пустыми модулями и `__main__.py`.

## 2. Модуль клиента ollama

- [ ] `ollama_client.py`: `OllamaClient` + ошибки `OllamaConnectionError`,
      `OllamaResponseError`.
- [ ] Unit-тесты: успешный `chat`, HTTP-ошибка, битый JSON, сетевой сбой,
      `DEFAULT_BASE_URL`.

## 3. Конфигурация

- [ ] `config.py`: `Config` (dataclass), загрузка TOML через `tomllib`,
      чтение env `OLLAMA_*`, валидация диапазонов.
- [ ] Unit-тесты: дефолты, приоритеты слоёв, отсутствующий файл — не ошибка,
      битый TOML — `ConfigError`.

## 4. Чтение входных файлов

- [ ] `input_doc.py`: `load_docs(input_dir)` — только `.md`/`.txt`,
      коллизии имён → `InputError`, пустая/отсутствующая папка → `InputError`.
- [ ] Unit-тесты: пустая папка, коллизия, пропуск чужих расширений,
      именование выходных файлов.

## 5. Рендер ответа

- [ ] `renderer.py`: `render(text, *meta, path)` — markdown-документ,
      обрамляющий ответ модели заголовком с именем исходного файла.
- [ ] Unit-тесты: структура выходного документа.

## 6. CLI и оркестрация

- [ ] `cli.py`: `main(argv) -> int`, обработка всех ошибок с exit codes
      0/2/3/4, ретраи при пустом ответе.
- [ ] Unit-тесты: сценарии для каждого exit code, перезапись выходных файлов.

## 7. Docker

- [ ] `Dockerfile` (мульти-стейдж, непривилегированный `app`).
- [ ] `compose.yaml` (сервисы `ollama` + `app`, volume, healthcheck),
      `compose.gpu.yaml`, `.env`, `.dockerignore`.
- [ ] Проверка: `docker compose up --build` обрабатывает пример из `input/`.

## 8. Dev Container

- [x] `.devcontainer/` (Python 3.13, uv, доступ к Docker).
- [ ] Проверка: «Reopen in Container» + `uv sync --dev`.

## 9. README и финальная проверка

- [ ] README: локальный запуск, Docker Compose, переменные окружения.
- [ ] `uv run pytest -m "not integration"`, `uv run ruff check .`,
      `uv run mypy src tests`.