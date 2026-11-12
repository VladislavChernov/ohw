# Чек-лист: Streamlit-рабочее место

## Документация

- [x] `proposal.md` — объём и границы
- [x] `design.md` — архитектура, экраны, поток данных
- [x] `specs/ui/spec.md` — EARS-требования
- [x] `openspec/project.md` — UI в границах и структуре
- [x] `SDD.md` — UI в области применения и компонентах
- [x] `README.md` — раздел UI и запуск
- [x] `docs/user-guide.md` — сценарий работы в UI
- [x] Коммит и push документации

## Код

- [x] `src/workflow/stages.py` — общие стадии
- [x] `src/workflow/main.py` — делегирование стадиям без изменения CLI
- [x] `src/ui/app.py` — шесть вкладок и боковая панель
- [x] `src/generator/pipeline.py` — правка промпта на файл из UI
- [x] `src/ui/launcher.py`, `src/ui/__main__.py` — запуск
- [x] `pyproject.toml` / `uv.lock` — streamlit
- [x] `Dockerfile` / `compose.yaml` — сервис `ui` на 8501

## Проверки

- [x] `tests/test_ui_app.py` — AppTest без сети
- [x] `tests/test_ui_app.py` — вкладка промпта и генерация с правкой
- [x] `uv run pytest`
- [x] `ruff check src scripts tests`
- [x] `mypy src`
- [x] Живой запуск UI в Docker и проверка вкладок
- [x] Коммит и push реализации
