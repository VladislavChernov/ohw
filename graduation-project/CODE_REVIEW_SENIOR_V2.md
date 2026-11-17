# Code Review: Graduation Project — Senior+ Уровень (v2)

## Состав команды ревью

| Роль | Имя (роль) | Зона ответственности |
|------|-----------|---------------------|
| Архитектор | Senior Backend Engineer | Дизайн пайплайна, контракты между стадиями, обработка ошибок |
| Инженер по качеству (NLP) | Senior ML/QA Engineer | Метрики читаемости, ясности, покрытия, консистентности |
| Инженер надёжности | Senior SRE | Ollama-интеграция, retry, continue-on-error, exit codes |
| Инженер по тестированию | Senior QA Engineer | Покрытие тестами, unit vs integration, фикстуры |

---

## 1. Обзор проекта

**Graduation Project** — генератор тестовой документации на основе локальной LLM (Ollama) с этапами:

`generate → assess → visualize → report`

Поддерживает 5 типов артефактов: `testcases`, `checklist`, `testplan`, `qareport`, `coveragematrix`. Есть CLI и Streamlit-интерфейс, оба используют общие функции из `src/workflow/stages.py`.

---

## 2. Сильные стороны

| # | Сильная сторона | Почему это важно |
|---|----------------|------------------|
| 1 | Общая кодовая база для CLI и UI (`stages.py`) | Два интерфейса не расходятся, единая логика пайплайна |
| 2 | Pydantic-контракты (`contracts.py`) | Валидация JSON-артефактов между стадиями, читаемые ошибки на русском |
| 3 | Версионирование пайплайна (`PIPELINE_VERSION`) | Защита от несовместимых артефактов между версиями |
| 4 | Конфигурируемые веса метрик (CLI + env + defaults) | Пользователь может настроить важность метрик без изменения кода |
| 5 | `continue-on-error` при `OllamaConnectionError` | Один файл не ломает весь прогон |
| 6 | `None` вместо `0` для невычислимых метрик | Честная отчётность, нет фальшивых нулей |
| 7 | Deterministic metrics (без LLM-судьи) | Повторяемость и прозрачность оценки |

---

## 3. senior+ замечания и рекомендации

### A. Архитектура и контракты

**Проблема 1: Дублирование метрик в pipeline.py и stages.py**

В `src/generator/pipeline.py` (строки 246-251) и в `src/workflow/stages.py` (строки 138-151) метрики считаются дважды: один раз при генерации, второй — при оценке. Это создаёт риск расхождения формул.

**Рекомендация:** Извлечь `compute_scenario_metrics(rendered, requirement_ids, generations, weights)` в один общий модуль (например, `src/quality/pipeline_metrics.py`) и вызывать из обоих мест.

**Проблема 2: `PipelineData` — «божественный объект»**

`PipelineData` хранит и сырой рендер, и кейсы, и метрики, и пути к графикам, и ошибки. Это затрудняет независимое тестирование стадий.

**Рекомендация:** Разделить на `GenerationSnapshot` (для `generation.json`) и `ReportData` (для `quality-metrics.json`), которые уже частично существуют через Pydantic, но не используются строго на границах стадий.

### B. Надёжность и обработка ошибок

**Проблема 3: OllamaConnectionError обрабатывается только в generate, но не в assess/visualize/report**

Если Ollama недоступна на стадии `generate`, при `--continue-on-error` ошибка записывается. Но если пользователь запустит только `--assess` на старом снимке, где нет сгенерированных артефактов, он получит `FileNotFoundError` без подсказки, что делать.

**Рекомендация:** Добавить в `main.py` блок `except FileNotFoundError` с явно named exit code (например, `EXIT_MISSING_INPUT = 6`) и сообщением «сначала запустите --generate».

**Проблема 4: Нет таймаута на отдельный вызов Ollama**

`httpx.Client` имеет общий `timeout`, но нет circuit-breaker или bulkhead. Если Ollama подвисает на одном файле, весь прогон ждёт полные 600 секунд.

**Рекомендация:** Добавить `per-call timeout` через `httpx.Timeout` или использовать `asyncio.wait_for`/`anyio.move_on_after` в `OllamaClient`.

### C. Метрики качества (NLP)

**Проблема 5: Readability — линейный штраф от 17 слов**

`assess_readability` linearly падает до 0 при отклонении 30 слов. Для технической документации (требования) длинные предложения — norma. Линейная шкала слишком жесткая.

**Рекомендация:** Сделать `READABILITY_TOLERANCE` и `OPTIMAL_WORDS_PER_SENTENCE` настраиваемыми через `--readability-tolerance` / env, или заменить на логистическую функцию.

**Проблема 6: Clarity goals — 5 категорий × 0.2 = максимально просто**

Каждая из 5 категорий даёт 0.2. Текст с одним «должен» и одним «ожидаем» уже набирает 0.4. Это не дифференцирует качество требований.

**Рекомендация:** Ввести веса по категориям (например, modality=0.3, expectation=0.2, verification=0.2, prohibition=0.15, result=0.15) и сделать их конфигурируемыми.

**Проблема 7: Internal consistency — heuristics 0.8 / 0.5**

Для одной генерации используется эвристика: последовательные BR-N → 0.8, с пробелом → 0.5. Это жестко задано и не объясняется в отчёте пользователю.

**Рекомендация:** Добавить в отчёт пометку `consistency_method: "internal_heuristic"` и сделать пороги настраиваемыми.

### D. Тестирование

**Проблема 8: Нет тестов для `pipeline.py` и `stages.py`**

Тесты есть для `test_quality.py`, `test_workflow.py`, но нет unit-тестов для `generate_scenarios`, `write_snapshot`, `load_snapshot`.

**Рекомендация:** Добавить `tests/test_pipeline.py` с фикстурами моков `OllamaClient` и тестами на `continue_on_error`, `dry_run`, version mismatch.

**Проблема 9: Интеграционные тесты требуют живую Ollama**

Тесты, которые Calling Ollama, должны быть явно помечены как `integration` и отделены от быстрых unit-тестов.

**Рекомендация:** Добавить маркер `@pytest.mark.integration` и конфигурацию `pytest.ini` с `--ignore` по умолчанию.

### E. Кодовое качество

**Проблема 10: Магические числа без констант**

`QUALITY_THRESHOLD = 0.5` в `models.py` — хорошо. Но в `assess_internal_consistency` числа `0.8` и `0.5` — магические.

**Рекомендация:** Вынести в константы `INTERNAL_CONSISTENCY_SEQUENTIAL = 0.8`, `INTERNAL_CONSISTENCY_GAP = 0.5`.

**Проблема 11: `streamlit` импортируется на верхнем уровне в `app.py`**

Это затрудняет импорт модуля в тестах без установленного streamlit.

**Рекомендация:** Отложенный импорт streamlit внутри функций или вынести UI-логику в отдельный пакет с опциональной зависимостью.

---

## 4. Конкретный план действий (Roadmap)

### Спринт 1 (P0 — критично)

1. **Вынести расчёт метрик в единый модуль** — убрать дублирование между `pipeline.py` и `stages.py`.
2. **Добавить `EXIT_MISSING_INPUT = 6`** для отсутствующего снимка генерации.
3. **Добавить `tests/test_pipeline.py`** — тесты на `continue_on_error`, `dry_run`, версионирование.
4. **Вынести магические числа консистентности в константы.**

### Спринт 2 (P1 — важно)

5. **Настроить `READABILITY_TOLERANCE`** через CLI/env.
6. **Взвесить ясность (clarity) по категориям** вместо равномерного 0.2.
7. **Отдельный импорт streamlit** для тестируемости `app.py`.
8. **Добавить `@pytest.mark.integration`** и разделить быстрые/медленные тесты.

### Спринт 3 (P2 — желательно)

9. **Разделить `PipelineData`** на `GenerationSnapshot` и `ReportData`.
10. **Circuit-breaker для Ollama** (per-call timeout).
11. **Помечать `consistency_method`** в отчёте.

---

## 5. Пример реализации (код)

### Единый модуль метрик (`src/quality/pipeline_metrics.py`)

```python
"""Единая точка расчёта метрик для generate и assess стадий."""

from src.quality.assessor import (
    assess_clarity_goals,
    assess_consistency,
    assess_internal_consistency,
    assess_readability,
    assess_scenario_coverage,
    weighted_overall,
)
from src.models import QualityMetrics


def compute_scenario_metrics(
    rendered: str,
    requirement_ids: list[str],
    generations: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> QualityMetrics:
    """Рассчитать все метрики для одного сгенерированного сценария."""
    readability = assess_readability(rendered)
    clarity = assess_clarity_goals(rendered)
    coverage = assess_scenario_coverage(rendered, requirement_ids)

    if generations and len(generations) > 1:
        consistency = assess_consistency(generations)
    else:
        consistency = assess_internal_consistency(rendered)

    overall = weighted_overall(
        {
            "readability": readability,
            "clarity_goals": clarity,
            "scenario_coverage": coverage,
            "consistency": consistency,
        },
        weights,
    )
    return QualityMetrics(
        readability=readability,
        clarity_goals=clarity,
        scenario_coverage=coverage,
        consistency=consistency,
        overall=overall,
    )
```

### Вызов из `pipeline.py` (generate стадия)

```python
from src.quality.pipeline_metrics import compute_scenario_metrics

# внутри цикла по документам:
quality = compute_scenario_metrics(
    rendered,
    requirement_ids,
    generations if len(generations) > 1 else None,
    weights,
)
```

### Вызов из `stages.py` (assess стадия)

```python
from src.quality.pipeline_metrics import compute_scenario_metrics

# внутри assess():
scenario.quality = compute_scenario_metrics(
    scenario.rendered,
    scenario.requirement_ids,
    generations if len(generations) > 1 else None,
    data.quality_weights or None,
)
```

---

## 6. Итоговая оценка

**Балл: 8.2 / 10**

Проект demonstriрует зрелый подход: общая кодовая база для CLI и UI, Pydantic-контракты, версионирование, конфигурируемые веса, честные `None` вместо нулей. Основные зоны роста senior+ — устранение дублирования логики метрик, выделение магических чисел в константы, расширение покрытия тестами для стадий пайплайна и настройка метрик читаемости/ясности.

Готов обсудить любые из пунктов и помочь с реализацией.

---