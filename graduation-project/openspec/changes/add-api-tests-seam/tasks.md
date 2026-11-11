# Чек-лист: шов API-тестов

## Документация

- [x] `proposal.md` — объём и границы
- [x] `design.md` — контракт, поток данных, CLI, ошибки
- [x] `specs/autotests/spec.md` — EARS-требования
- [x] `openspec/project.md` — границы и будущая структура
- [x] `SDD.md` — раздел «Точка расширения: API-тесты»
- [x] `README.md` — раздел «Возможное развитие»
- [x] `docs/user-guide.md` — портативный запуск через `uv`
- [ ] Коммит и push документации

## Код

- [ ] `src/autotests/models.py` — `ApiTestRequest`, `ApiCaseResult`, `ApiTestReport`
- [ ] `src/autotests/ports.py` — `ApiTestRunner`, `ApiTestError`
- [ ] `src/autotests/disabled.py` — заглушка раннера
- [ ] `src/autotests/service.py` — запуск и запись `api-tests.json`
- [ ] `src/models.py` — необязательное поле `api_tests` и сериализация
- [ ] `src/workflow/main.py` — флаги, фаза, коды возврата
- [ ] `src/reporting/report_generator.py` — раздел `## API-тесты`
- [ ] `tests/test_autotests.py` — фейковый раннер, JSON, отчёт, отсутствие флага

## Проверки

- [ ] `uv run pytest`
- [ ] `ruff check src scripts tests`
- [ ] `mypy src`
- [ ] Живой прогон без `--api-tests`: артефакты не изменились
- [ ] Коммит и push реализации
