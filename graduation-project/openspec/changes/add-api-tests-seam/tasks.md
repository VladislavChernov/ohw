# Чек-лист: шов API-тестов

## Документация

- [x] `proposal.md` — объём и границы
- [x] `design.md` — контракт, поток данных, CLI, ошибки
- [x] `specs/autotests/spec.md` — EARS-требования
- [x] `openspec/project.md` — границы и будущая структура
- [x] `SDD.md` — раздел «Точка расширения: API-тесты»
- [x] `README.md` — раздел «Возможное развитие»
- [x] `docs/user-guide.md` — портативный запуск через `uv`
- [x] Коммит и push документации

## Код

- [x] `src/autotests/models.py` — `ApiTestRequest`, `ApiCaseResult`, `ApiTestReport`
- [x] `src/autotests/ports.py` — `ApiTestRunner`, `ApiTestError`
- [x] `src/autotests/disabled.py` — заглушка раннера
- [x] `src/autotests/service.py` — запуск и запись `api-tests.json`
- [x] `src/models.py` — необязательное поле `api_tests` и сериализация
- [x] `src/workflow/main.py` — флаги, фаза, коды возврата
- [x] `src/reporting/report_generator.py` — раздел `## API-тесты`
- [x] `tests/test_autotests.py` — фейковый раннер, JSON, отчёт, отсутствие флага

## Проверки

- [x] `uv run pytest` — 93 passed, 1 skipped
- [x] `ruff check src scripts tests`
- [x] `mypy src`
- [x] Живой прогон без `--api-tests`: `report.md` и `quality-metrics.json`
  побайтово совпали с `examples/live-run`, `api-tests.json` не создан
- [ ] Коммит и push реализации
