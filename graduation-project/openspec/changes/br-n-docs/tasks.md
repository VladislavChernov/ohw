# Задачи: документация `BR-N` и эвристика консистентности

## Документация

- [x] `openspec/changes/br-n-docs/proposal.md`
- [x] `openspec/changes/br-n-docs/design.md`
- [x] `openspec/changes/br-n-docs/tasks.md`
- [x] `openspec/specs/quality/spec.md` — EARS-требование и WHEN/THEN
- [x] `README.md` — раздел «Идентификаторы BR-N»
- [x] `docs/metrics.md` — три исхода эвристики
- [x] `SDD.md` — раздел 5.4 и история документа
- [x] `openspec/project.md` — активное изменение
- [x] `CHANGE_PROPOSAL.md` — пункт отмечен реализованным
- [x] `CHANGELOG.md` — раздел `Unreleased`

## Код и тесты

- [x] `src/quality/assessor.py` — `None` при менее чем двух ids
- [x] `tests/test_quality.py` — граничные случаи и `assess_quality`

## Проверки

- [x] `uv run pytest` — `204 passed, 2 skipped`
- [x] `uv run ruff check src tests scripts`
- [x] `uv run mypy src`
- [x] CI на `master` зелёный (run #109)
