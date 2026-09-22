# Калибровка судьи в Dz4: текущее состояние и рекомендации

> Аналитический мини-отчёт. Полное руководство — в `learning/judge_calibration_methods_learning.md`. Связь с IT-контекстом — через MQM-кейс в том же файле.

---

## 1. Текущее состояние

Судья в Dz4 = `generation_metrics()` (`eval/metrics.py:63-95`):
- вычисляет `groundedness` и `usefullness` (да — с опечаткой, судя по коду);
- rubric находится в `prompts/generation_metrics.jinja`;
- вызывается из pipeline (`pipeline.py:166`) → запись в `done`.

**Проверка валидности судьи почти не реализована:**

| Что нужно для калибровки | Где есть в планах | Где есть в коде |
|---|---|---|
| gold-набор | `test_plan.md §7.4` (упоминание) | **нет** — есть только `questions.json` (50 контрольных вопросов, без человеческих оценок) |
| A/B swap test | — | **нет** |
| Determism check (T=0, stdev) | `test_plan.md §6.1` | **нет** |
| Drift schedule | — | **нет** |

---

## 2. Что конкретно не хватает

1. **Нет gold set:** `questions.json` — это вопросы с ответами, а не вопросы с `gold-score` от людей. Мета-оценка судьи требует **чужих оценок**, а не ответов.
2. **Нет скрипта калибровки:** нет `test_judge_calibration.py` или аналога.
3. **Нет регламента drift detection:** проверяется только «перед выпуском», а не по расписанию.

---

## 3. Рекомендации

1. **Создать gold-набор из 20–50 вопросов из `questions.json`** с human-оценкой groundedness/usefullness (3-балльная шкала) → использовать для Spearman/κ.
2. **Перепроверить rubric в `generation_metrics.jinja`** на A/B swap (перестановка A/B в промпте) → убедиться, что score не дрейфует.
3. **Добавить determism check:** минимум 3 прогона на T=0 → stdev(score) < 0.05.
4. **Настроить drift detection:** weekly re-run gold на фиксированном наборе → порог ρ падения 10%.
5. **MQM-параллель:** как gold в IT-опросах выстраивается по 7-уровневой MQM-шкале ошибок → можно адаптировать rubric groundedness/usefullness к 7-уровневой → см. `learning/judge_calibration_methods_learning.md` §2.

---

## 4. Связь с другими аналитиками

- **Основной методичкой:** `learning/llm_as_judge_learning.md` §3.3 → «Мета-оценка как сущность».
- **Полное руководство:** `learning/judge_calibration_methods_learning.md` §1–§5.
- **MQM-кейс (IT-опросы):** `learning/judge_calibration_methods_learning.md` §2 → как выстроить gold по 7-уровневой шкале ошибок.
