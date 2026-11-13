# Чек-лист: устойчивый пайплайн (P0 ревью)

## Документация

- [ ] `proposal.md` — объём, границы, поведение по умолчанию
- [ ] `design.md` — версия, матрица чтения, обработка ошибок Ollama
- [ ] `openspec/specs/pipeline/spec.md` — EARS-требования к контракту и recovery
- [ ] `openspec/specs/generation/spec.md` — `--continue-on-error` вместо fail-fast
- [ ] `openspec/project.md` — активное изменение и границы
- [ ] `SDD.md` — версии артефактов, флаг, тесты
- [ ] `README.md` и `docs/user-guide.md` — CLI-флаг и legacy-поведение
- [ ] Коммит и push документации

## Код

- [ ] `src/config.py` — `PIPELINE_VERSION`
- [ ] `src/workflow/stages.py` — версия в снимке и валидация при чтении
- [ ] `src/reporting/report_generator.py` — версия в JSON-отчёте и валидация
- [ ] `src/generator/pipeline.py` — `continue_on_error` в `GenerationRequest`
- [ ] `src/workflow/main.py` — `--continue-on-error`, мягкая проверка модели
- [ ] `src/ui/app.py` — чекбокс продолжения при ошибке Ollama

## Проверки

- [ ] Тесты версии, legacy и mismatch для обоих файлов
- [ ] Тесты fail-fast и per-file recovery
- [ ] Тесты CLI-флага и UI-чекбокса
- [ ] `uv run pytest -q`
- [ ] `uv run ruff check src tests scripts`
- [ ] `uv run mypy src`
- [ ] Живой e2e на `qwen2.5:7b-instruct`, сверка с `examples/live-run/`
- [ ] Коммит и push кода
- [ ] Тег `v0.1.0`
