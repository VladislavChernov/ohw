# Задачи: режим `--dry-run`

## Документация

- [x] `openspec/changes/dry-run/proposal.md` — мотивация и критерии
- [x] `openspec/changes/dry-run/design.md` — формат вывода и CLI
- [x] `openspec/changes/dry-run/tasks.md` — этот файл
- [x] `openspec/specs/generation/spec.md` — EARS-требования к dry-run
- [x] `openspec/project.md` — активное изменение
- [x] `README.md` и `docs/user-guide.md` — как пользоваться флагом
- [x] `SDD.md` — стадия, CLI, ошибки
- [x] `CHANGELOG.md` — раздел `Unreleased`

## Код

- [x] `src/generator/pipeline.py` — общий helper подготовки и
      `build_prompt_previews`
- [x] `src/workflow/stages.py` — `dry_run` и запись `output/prompts/`
- [x] `src/workflow/main.py` — флаг, выбор фазы, отказ от сетевой проверки

## Тесты

- [x] совпадение промпта с обычным прогоном по SHA-256
- [x] отсутствие вызовов Ollama и `generation.json`
- [x] конфликтующие фазы дают код `2`
- [x] `coveragematrix` содержит пояснение в манифесте

## Проверки

- [x] `uv run pytest` — `199 passed, 2 skipped`
- [x] `uv run ruff check src tests scripts`
- [x] `uv run mypy src`
- [x] CI на `master` зелёный (run #107)
