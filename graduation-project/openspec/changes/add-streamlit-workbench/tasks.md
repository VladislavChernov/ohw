# Чек-лист: Streamlit-рабочее место

## Документация

- [x] `proposal.md` — объём и границы
- [x] `design.md` — архитектура, экраны, поток данных
- [x] `specs/ui/spec.md` — EARS-требования
- [x] `openspec/project.md` — UI в границах и структуре
- [x] `SDD.md` — UI в области применения и компонентах
- [ ] `README.md` — раздел UI и запуск
- [ ] `docs/user-guide.md` — сценарий работы в UI
- [ ] Коммит и push документации

## Код

- [ ] `src/workflow/stages.py` — общие стадии
- [ ] `src/workflow/main.py` — делегирование стадиям без изменения CLI
- [ ] `src/ui/app.py` — пять вкладок и боковая панель
- [ ] `src/ui/launcher.py`, `src/ui/__main__.py` — запуск
- [ ] `pyproject.toml` / `uv.lock` — streamlit
- [ ] `Dockerfile` / `compose.yaml` — сервис `ui` на 8501

## Проверки

- [ ] `tests/test_ui_app.py` — AppTest без сети
- [ ] `uv run pytest`
- [ ] `ruff check src scripts tests`
- [ ] `mypy src`
- [ ] Живой запуск UI в Docker и проверка вкладок
- [ ] Коммит и push реализации
