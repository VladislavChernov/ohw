# Задачи: веса и сброс состояния в UI

## Документация

- [x] `openspec/changes/ui-weights-and-reset/proposal.md`
- [x] `openspec/changes/ui-weights-and-reset/design.md`
- [x] `openspec/changes/ui-weights-and-reset/tasks.md`
- [x] `openspec/specs/ui/spec.md` — веса, валидация, сброс
- [x] `openspec/project.md` — активное изменение
- [x] `README.md`, `docs/user-guide.md`, `CONCEPT.md`, `SDD.md`, `CHANGELOG.md`

## Код

- [x] `src/ui/app.py` — `STATE_DEFAULTS`, панель весов, валидация, сброс,
      передача весов в `GenerationRequest`, исправление номеров вкладок

## Тесты

- [x] кастомные веса попадают в `generation.json`
- [x] неверная сумма не запускает генерацию
- [x] кнопка сброса очищает состояние
- [x] подсказки вкладок исправлены

## Проверки

- [x] `uv run pytest` — `224 passed, 1 skipped` (финальная проверка)
- [x] `uv run ruff check src tests scripts`
- [x] `uv run mypy src`
- [x] `uv lock --check`
- [x] CI на `master` зелёный (run #126)
