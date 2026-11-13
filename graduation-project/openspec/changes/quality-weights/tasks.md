# Чек-лист: настраиваемые веса overall (P1)

## Документация

- [x] `proposal.md` — объём, границы, критерии приёмки
- [x] `design.md` — формат, приоритет, валидация, поток данных
- [x] `openspec/specs/quality/spec.md` — настраиваемые веса и валидация
- [x] `openspec/project.md` — активное изменение
- [x] `SDD.md` — CLI, env, форматы JSON, тесты
- [x] `README.md` и `docs/user-guide.md` — флаг и примеры
- [x] `docs/metrics.md` — веса по умолчанию и переопределение
- [x] Коммит и push документации

## Код

- [x] `src/config.py` — веса по умолчанию, парсинг и валидация
- [x] `src/models.py` — `quality_weights` в `PipelineData` и JSON
- [x] `src/quality/assessor.py` — веса в `weighted_overall`/`assess_quality`
- [x] `src/generator/pipeline.py` — веса в `GenerationRequest`
- [x] `src/workflow/stages.py` — сохранение и повторное использование весов
- [x] `src/workflow/main.py` — `--quality-weights`
- [x] `src/reporting/report_generator.py` — строка весов в отчёте

## Проверки

- [x] Тесты парсинга, приоритета CLI/env и валидации
- [x] Тесты расчёта `overall` с пользовательскими весами
- [x] Тесты сериализации и legacy-файлов без `quality_weights`
- [x] Тест CLI-флага и пересчёта через `assess`
- [x] `uv run pytest -q`
- [x] `uv run ruff check src tests scripts`
- [x] `uv run mypy src`
- [x] Коммит и push кода
