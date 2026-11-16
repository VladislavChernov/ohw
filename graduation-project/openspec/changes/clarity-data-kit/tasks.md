# Задачи: инструменты для сбора данных по clarity

## Документация

- [x] `openspec/changes/clarity-data-kit/proposal.md` — мотивация и границы
- [x] `openspec/changes/clarity-data-kit/design.md` — формат, анализ, ограничения
- [x] `openspec/changes/clarity-data-kit/tasks.md` — этот файл
- [x] `docs/clarity-annotation.md` — гайд разметчика
- [x] `docs/metrics.md` — ссылка на гайд без изменения формулы
- [x] `README.md` — упоминание инструментов
- [x] `SDD.md` — модуль, тесты, история документа

## Код

- [x] `src/quality/assessor.py` — `CLARITY_SIGNAL_NAMES` и `clarity_signals`
      без изменения `assess_clarity_goals`
- [x] `src/quality/clarity_kit.py` — экспорт, разбор разметки, kappa, корреляции
- [x] `scripts/export_clarity_sample.py` — CLI выгрузки
- [x] `scripts/analyze_clarity.py` — CLI анализа

## Тесты

- [x] `tests/test_clarity_kit.py` — экспорт, JSONL, kappa, корреляции, CLI
- [x] формула `clarity` не изменилась (сравнение с аксессорами)

## Проверки

- [ ] `uv run pytest` — локальный Docker недоступен, проверяет CI
- [ ] `uv run ruff check src tests scripts` — проверяет CI
- [ ] `uv run mypy src` — проверяет CI
- [ ] CI на `master` зелёный
