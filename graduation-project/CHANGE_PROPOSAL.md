# Предложение по изменению: Senior+ Code Review

## Проект: Graduation Project
Система генерации/оценки тестовой документации через локальную LLM (Ollama).
CLI + Streamlit интерфейсы. 5 типов артефактов (testcases, checklist, testplan, qareport, coveragematrix).

**Текущий балл**: 7.5/10

## P0 Критические улучшения (спринт 1-2)

### 1. Pipeline versioning
- Добавить `pipeline_version` в generation.json
- Валидировать в load_snapshot/load_pipeline_data
- Fallback при mismatch версии

### 2. Error recovery (per-file handling)
- Изменить generate_scenarios:continuе при OllamaError, а не fail-fast
- CLI флаг `--continue-on-error`
- Обновить отчет об ошибках

## P1 Улучшения качества (спринт 3-4)

### 3. Configurable quality weights
- `--quality-weights` CLI option
- `GRADUATION_QUALITY_WEIGHTS` env var
- Валидация: сумма = 1.0
- Обновить assessor.py

### 4. Clarity metric review
- Пересмотреть 5_patterns × 0.2
- Weight based on rarity или custom weight
- Обновить документацию

### 5. Schema validation
- Добавить jsonschema зависимость
- Schema файлы для generation.json и quality-metrics.json
- Валидация при загрузке

## P2 Удобство и документация (спринт 5-6)

### 6. Readability tolerance
- `--readability-tolerance` CLI
- `GRADUATION_READABILITY_TOLERANCE` env var

### 7. BR-N numbering docs
- Документировать в README
- Обновить assess_internal_consistency

### 8. Streamlit state management
- Кнопка "Clear cache"
- LRU limit для session state

### 9. Dry-run flag
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

## Пример кода (пipeline versioning)

```python
# write_snapshot: добавить pipeline_version
snapshot_data["pipeline_version"] = "0.1.0"

# load_snapshot: валидировать версию
if raw.get("pipeline_version") != "0.1.0":
    raise ValueError("Version mismatch")
```