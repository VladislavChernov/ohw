# Чек-лист: устойчивый пайплайн (P0 ревью)

## Документация

- [x] `proposal.md` — объём, границы, поведение по умолчанию
- [x] `design.md` — версия, матрица чтения, обработка ошибок Ollama
- [x] `openspec/specs/pipeline/spec.md` — EARS-требования к контракту и recovery
- [x] `openspec/specs/generation/spec.md` — `--continue-on-error` вместо fail-fast
- [x] `openspec/project.md` — активное изменение и границы
- [x] `SDD.md` — версии артефактов, флаг, тесты
- [x] `README.md` и `docs/user-guide.md` — CLI-флаг и legacy-поведение
- [x] Коммит и push документации

## Код

- [x] `src/config.py` — `PIPELINE_VERSION`
- [x] `src/workflow/stages.py` — версия в снимке и валидация при чтении
- [x] `src/reporting/report_generator.py` — версия в JSON-отчёте и валидация
- [x] `src/generator/pipeline.py` — `continue_on_error` в `GenerationRequest`
- [x] `src/workflow/main.py` — `--continue-on-error`, мягкая проверка модели
- [x] `src/ui/app.py` — чекбокс продолжения при ошибке Ollama

## Проверки

- [x] Тесты версии, legacy и mismatch для обоих файлов
- [x] Тесты fail-fast и per-file recovery
- [x] Тесты CLI-флага и UI-чекбокса
- [x] `uv run pytest -q` — 105 passed, 1 skipped
- [x] `uv run ruff check src tests scripts`
- [x] `uv run mypy src`
- [x] Живой e2e на `qwen2.5:7b-instruct`, сверка с `examples/live-run/`
- [x] Коммит и push кода
- [ ] Тег `v0.1.0`
