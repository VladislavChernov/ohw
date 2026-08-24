# Project: ai-testcase-generator

Учебный проект (Otus, ДЗ2): CLI-утилита на Python, генерирующая тест-кейсы
для веб-сайта с помощью локальной LLM через ollama.

## Стек

- Python 3.13, пакетный менеджер **uv** (lock-файл коммитится).
- HTTP: `httpx`; схемы: `pydantic`; тесты: `pytest`.
- Инференс: существующий ollama-контейнер пользователя, GPU NVIDIA RTX 2070 SUPER.
- Приложение запускается в Docker; разработка — VS Code Dev Container.

## Конвенции

- Спеки в формате OpenSpec: `openspec/changes/<change>/{proposal,design,tasks}.md`
  + дельты в `specs/<capability>/spec.md` (требования EARS: SHALL + WHEN/THEN сценарии).
- Код и комментарии — на английском; документация (README, спеки) — на русском.
- Exit codes CLI: 0 ок, 2 аргументы/файлы, 3 ollama недоступна, 4 не удалось
  получить валидный результат.

## Окружение

- `OLLAMA_BASE_URL` — адрес ollama (по умолчанию `http://host.docker.internal:11434`).
- `OLLAMA_MODEL` — имя установленной модели (обязательно).
