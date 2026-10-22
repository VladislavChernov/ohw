# Tasks: add-reports

## 1. Планирование

- [x] Зафиксировать недостоверность старого `red-team.md`.
- [x] Определить структуру `reports/<run-id>/` и схему manifest.
- [x] Определить правила агрегации и публикации `latest.md`.

## 2. Генератор

- [ ] `reports_builder/` (Python): загрузка `results.jsonl`, валидация.
- [ ] `manifest.py` — проверка обязательных полей.
- [ ] `aggregate.py` — counts, verdict, latency p50/p95, CPU/RAM.
- [ ] `render.py` — `summary.md`, `latest.md`, `red-team.md`.
- [ ] `build` CLI: `python -m report_builder build --run reports/<id>`.
- [ ] Атомарная запись (temp dir + rename).

## 3. Правила

- [ ] `n<3` → `unknown`.
- [ ] `bypass` + `blocked` → `mixed`.
- [ ] `error` не считается `blocked`.
- [ ] `complete=false` не публикуется как `latest`.
- [ ] `not_applicable` только по явному флагу сценария.
- [ ] Сравнение прогонов только при совпадении `scenario_set_sha256`.

## 4. Red-team

- [ ] Таблица 16 × 5 из `summary.json`.
- [ ] `unknown`/`mixed`/`error` видны.
- [ ] Убрать ручные «all blocked» и недоказанные findings.
- [ ] Оставить scope/environment/recommendations, если они не противоречат
      данным.

## 5. Проверка

- [ ] Сгенерировать отчёт из тестового `results.jsonl` (fixture).
- [ ] Проверить `n=1` → `unknown`.
- [ ] Проверить `mixed`.
- [ ] Проверить `complete=false` не перезаписывает `latest.md`.
- [ ] Проверить отсутствие секретов в опубликованных файлах.
- [ ] Сгенерировать отчёт из реального smoke-прогона.

## 6. Завершение

- [ ] `reports/latest.md` и `reports/red-team.md` соответствуют данным.
- [ ] README: как сгенерировать отчёт.
- [ ] Коммит только `dz5/` после явной просьбы.
