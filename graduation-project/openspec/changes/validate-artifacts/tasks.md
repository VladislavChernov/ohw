# Задачи: валидация артефактов через pydantic

## Документация

- [x] `openspec/changes/validate-artifacts/proposal.md` — мотивация и объём
- [x] `openspec/changes/validate-artifacts/design.md` — схемы, ошибки, совместимость
- [x] `openspec/changes/validate-artifacts/tasks.md` — этот файл
- [x] `openspec/specs/pipeline/spec.md` — EARS-требования к валидации артефактов
- [x] `openspec/project.md` — активное изменение
- [x] `SDD.md` — раздел о контрактах, таблица ошибок, тесты
- [x] `docs/user-guide.md` — поведение при повреждённых файлах
- [x] `README.md` — краткое упоминание валидации
- [x] `CHANGE_PROPOSAL.md` — пункт про schema validation отмечен

## Код

- [x] `src/contracts.py` — pydantic-модели и чтение JSON с ошибками
- [x] `src/models.py` — построение dataclass-ов из валидированных документов
- [x] `src/workflow/stages.py` — `load_snapshot` через `read_json_object`
- [x] `src/reporting/report_generator.py` — `load_pipeline_data` через
      `read_json_object`

## Тесты

- [x] `tests/test_contracts.py` — валидный документ, отсутствующее `filename`,
      неверный тип, лишние поля, `null`-поля, битый JSON
- [x] `tests/test_workflow.py` — `--assess` на повреждённом снимке → код `2`
- [x] `tests/test_workflow.py` — `--report` на повреждённом JSON-отчёте → код `2`
- [x] Legacy-файлы `examples/live-run/` читаются без ошибок

## Проверки

- [x] `uv run pytest` — `172 passed, 1 skipped`
- [x] `uv run ruff check src tests scripts`
- [x] `uv run mypy src`
- [x] Повреждённые снимки дают код `2` без traceback (ручная проверка CLI)
- [ ] CI на `master` зелёный
