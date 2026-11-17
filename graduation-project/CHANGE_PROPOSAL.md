# Предложение по изменению: Senior+ Code Review

## Проект: Graduation Project
Система генерации/оценки тестовой документации через локальную LLM (Ollama).
CLI + Streamlit интерфейсы. 5 типов артефактов (testcases, checklist, testplan, qareport, coveragematrix).

**Текущий балл**: 7.5/10

## P0 Критические улучшения (спринт 1-2)

### 1. Pipeline versioning — реализовано в 0.1.0
- Добавить `pipeline_version` в generation.json
- Валидировать в load_snapshot/load_pipeline_data
- Fallback при mismatch версии

### 2. Error recovery (per-file handling) — реализовано в 0.1.0
- Изменить generate_scenarios: continue при OllamaError, а не fail-fast
- CLI флаг `--continue-on-error`
- Обновить отчет об ошибках

## P1 Улучшения качества (спринт 3-4)

### 3. Configurable quality weights — реализовано в 0.2.0
- `--quality-weights` CLI option
- `GRADUATION_QUALITY_WEIGHTS` env var
- Валидация: сумма = 1.0
- Обновить assessor.py

### 4. Clarity metric review — ОТЛОЖЕНО до данных разметчиков
- Пересмотреть 5_patterns × 0.2
- Weight based on rarity или custom weight
- Обновить документацию
- Подготовлены инструменты разметки (`docs/clarity-annotation.md`); без данных метрика не меняется
- Сухой прогон на двух моделях завершён: это проверка контура, а не человеческая эталонная разметка
- **Триггер возврата:** получены файлы разметки от двух людей (100–150 фрагментов)
- **При возврате:** проверить Cohen κ ≥ 0.6 → разобрать расхождения → вывести веса
  на train → сравнить с равными на holdout → только потом менять формулу, тесты,
  `openspec/specs/quality/spec.md`, `docs/metrics.md`, SDD и CONCEPT
- Если выигрыша на holdout нет — оставить `0.2 × 5` и записать это решение
- **Решение от 2026-11-17:** формулу не менять. Доступен только один разметчик,
  14 заполненных блоков дают 10 уникальных фрагментов после дедупликации;
  `Cohen κ` и взвешенный kappa — `n/a`, корреляция с метрикой тоже `n/a`
  из-за нулевой дисперсии. Оснований для смены весов нет; триггер возврата остаётся
  в силе при появлении двух human-разметчиков

### 5. Schema validation — реализовано в 0.3.0 через pydantic
- Валидация `generation.json` и `quality-metrics.json` при загрузке
- Модели `SnapshotDocument`/`ReportDocument` вместо отдельной `jsonschema`
- Ошибки с именем файла и путём до поля, код возврата 2

## P2 Удобство и документация (спринт 5-6)

### 6. Readability tolerance — ОТЛОЖЕНО
- `--readability-tolerance` CLI
- `GRADUATION_READABILITY_TOLERANCE` env var
- Причина: нет данных о связи длины предложения с читаемостью для русского
  и английского; без них это кручение ручки, а не настройка метрики

### 7. BR-N numbering docs — реализовано в 0.4.0
- Документировать в README
- Обновить assess_internal_consistency

### 8. Streamlit state management — частично реализовано в 0.7.0 (clear)
- Кнопка "Clear cache" → «Очистить состояние» — реализована
- LRU limit для session state — не реализован: состояние маленькое, явного
  сброса достаточно; вернёмся при десятках крупных прогонов

### 9. Dry-run flag — реализовано в 0.4.0
- `--dry-run` в main.py
- Генерировать промпты без Ollama
- Локальная валидация

## Ожидаемые преимущества
1. Надежность: versioning предотвращает silent breakdowns
2. UX: per-file error continuation
3. Настраиваемость: weights без кода
4. Качество: schema validation
5. Тестовость: лучшее покрытие unit tests
6. Документированность: четкие конвенции

## Пример кода (pipeline versioning)

```python
# write_snapshot: добавить pipeline_version
snapshot_data["pipeline_version"] = "0.1.0"

# load_snapshot: валидировать версию
if raw.get("pipeline_version") != "0.1.0":
    raise ValueError("Version mismatch")
```