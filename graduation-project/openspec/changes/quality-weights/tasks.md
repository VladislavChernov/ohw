# Чек-лист: настраиваемые веса overall (P1)

## Документация

- [ ] `proposal.md` — объём, границы, критерии приёмки
- [ ] `design.md` — формат, приоритет, валидация, поток данных
- [ ] `openspec/specs/quality/spec.md` — настраиваемые веса и валидация
- [ ] `openspec/project.md` — активное изменение
- [ ] `SDD.md` — CLI, env, форматы JSON, тесты
- [ ] `README.md` и `docs/user-guide.md` — флаг и примеры
- [ ] `docs/metrics.md` — веса по умолчанию и переопределение
- [ ] Коммит и push документации

## Код

- [ ] `src/config.py` — веса по умолчанию, парсинг и валидация
- [ ] `src/models.py` — `quality_weights` в `PipelineData` и JSON
- [ ] `src/quality/assessor.py` — веса в `weighted_overall`/`assess_quality`
- [ ] `src/generator/pipeline.py` — веса в `GenerationRequest`
- [ ] `src/workflow/stages.py` — сохранение и повторное использование весов
- [ ] `src/workflow/main.py` — `--quality-weights`
- [ ] `src/reporting/report_generator.py` — строка весов в отчёте

## Проверки

- [ ] Тесты парсинга, приоритета CLI/env и валидации
- [ ] Тесты расчёта `overall` с пользовательскими весами
- [ ] Тесты сериализации и legacy-файлов без `quality_weights`
- [ ] Тест CLI-флага и пересчёта через `assess`
- [ ] `uv run pytest -q`
- [ ] `uv run ruff check src tests scripts`
- [ ] `uv run mypy src`
- [ ] Коммит и push кода
