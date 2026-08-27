# Project: light-llm-engine

Учебный проект (Otus): простой движок на Python, который читает файл запроса из
директории `input`, отправляет его содержимое локальной LLM, поднятой в Docker
(ollama), и записывает ответ модели в директорию `output`. Сам движок тоже
запускается в Docker.

## Стек

- Python 3.13, пакетный менеджер **uv** (lock-файл коммитится).
- HTTP: `httpx`; схемы: `pydantic` (при необходимости); тесты: `pytest`.
- Инференс: контейнер `ollama/ollama`, GPU NVIDIA (RTX 2070 SUPER) или CPU.
- Приложение запускается в Docker; разработка — VS Code Dev Container.

## Конвенции

- Спеки в формате OpenSpec: `openspec/changes/<change>/{proposal,design,tasks}.md`
  + дельты в `specs/<capability>/spec.md` (требования EARS: ДОЛЖНА/ОБЯЗАНА
  + WHEN/THEN сценарии).
- Код и комментарии — на английском; документация (README, спеки) — на русском.
- Exit codes CLI: 0 — успех, 2 — ошибки аргументов/входных файлов,
  3 — ollama недоступна, 4 — не удалось получить валидный ответ.

## Окружение

- `OLLAMA_BASE_URL` — адрес сервиса ollama
  (по умолчанию `http://localhost:11434`, внутри compose — `http://ollama:11434`).
- `OLLAMA_MODEL` — имя установленной модели (обязательно).