# Демо для сдачи выпускного проекта

> Сценарий на 7–10 минут: показать полный путь от требований до отчёта,
> рабочее место UI и принятые инженерные решения. Все команды и цифры
> соответствуют версии 0.7.0. Подробности — в [`demo.md`](demo.md),
> [`defense-checklist.md`](defense-checklist.md) и [`CONCEPT.md`](../CONCEPT.md).
> Актуализировано: 2026-11-17.

---

## 0. Подготовка за 30 секунд

Рассказ одним абзацем:

> Проект по файлам требований `.md`/`.txt` генерирует тестовую документацию
> пяти типов локальной моделью `qwen2.5:7b-instruct` и оценивает её четырьмя
> детерминированными метриками. CLI и Streamlit используют одни и те же стадии,
> артефакты между фазами валидируются pydantic-контрактами, веса `overall`
> настраиваются и сохраняются вместе с результатами. LLM-судьи нет.

Показать структуру:

```text
input/ → generate → scenarios/*.md + generation.json
              ↓
          assess → quality-metrics.json
              ↓
        visualize → charts/*.png
              ↓
          report → report.md
```

---

## 1. Запуск стенда

```powershell
cd D:\Otus\ohw\graduation-project
.\run.ps1 -Gpu          # NVIDIA; .\run.ps1 — CPU
```

Что сказать:

- общий стенд живёт в `infra/`: `ohw-ollama`, образ `ohw-python:3.13`, сеть `ohw_net`;
- модель уже лежит в volume `ohw_ollama_models`, повторной загрузки нет;
- `run.ps1 -Gpu` проверяет nvidia-рантайм и подключает `compose.gpu.yaml`;
- после проверки стенд выключается, volume сохраняется.

Если Docker поднимать нельзя — показать офлайн-часть из раздела 4.

---

## 2. CLI: полный пайплайн

```bash
python -m src.workflow \
  --input-dir input --output-dir output/live \
  --count 4 --runs 2 --temperature 0.2
```

Ожидаемый вывод (сокращённо):

```text
фазы: generate -> assess -> visualize -> report
генерация: input -> output/live/scenarios
  (тип=testcases, кейсов=4, повторов=2, модель=qwen2.5:7b-instruct)
веса overall: readability=0.25, clarity_goals=0.25, scenario_coverage=0.30, consistency=0.20
оценка: overall=0.646 (1/1 сценариев успешны)
отчёт: output/live/report.md
данные: output/live/quality-metrics.json
```

Показать по порядку:

1. `output/live/scenarios/registration-requirements.md` — сам артефакт, 4 тест-кейса.
2. `output/live/generation.json` — `pipeline_version`, `quality_weights`, `generations`, `prompt`.
3. `output/live/quality-metrics.json` — метрики и реальные `case_summaries`.
4. `output/live/report.md` — таблица метрик, строка весов, детализация, графики.
5. `output/live/charts/*.png` — три графика.

Метрики эталонного прогона: readability `0.629`, clarity `0.600`, coverage `0.800`,
consistency `0.495`, overall `0.646`.

---

## 3. Кастомные веса: показать политику, а не только код

```bash
python -m src.workflow \
  --input-dir input --output-dir output/weights \
  --count 4 --runs 2 --temperature 0.2 \
  --quality-weights readability=0.40,clarity_goals=0.20,scenario_coverage=0.20,consistency=0.20
```

В `report.md` должна появиться строка:

```text
| Веса overall | readability=0.40, clarity_goals=0.20, scenario_coverage=0.20, consistency=0.20 |
```

Что сказать:

- приоритет: `--quality-weights` → `GRADUATION_QUALITY_WEIGHTS` → defaults;
- веса валидируются: четыре метрики, значения `≥ 0`, сумма `1.0`;
- веса сохраняются в обоих JSON, поэтому `--assess` пересчитывает метрики
  независимо от текущего окружения;
- при том же наборе метрик `overall` меняется: `0.646` → `0.631`
  (`0.4×0.629 + 0.2×0.6 + 0.2×0.8 + 0.2×0.495`).

Пример ошибки (должен вернуть код `2`):

```bash
python -m src.workflow --generate --quality-weights readability=1.0
```

---

## 4. Офлайн-проверки без Ollama

```bash
# Промпты без модели и без generation.json
python -m src.workflow --dry-run --input-dir input --output-dir output/prompts

# Пересчёт метрик по уже сохранённому снимку
python -m src.workflow --assess --output-dir output/live

# Живой e2e, если стенд поднят
RUN_OLLAMA_INTEGRATION=1 uv run pytest -m integration
```

Зафиксированный результат integration: `1 passed, 208 deselected in 13.86s`,
`overall = 0.382`, `consistency = 0.618` при `runs = 2`.

`output/prompts/` содержит `<stem>.system.txt`, `<stem>.user.txt` и `manifest.json`
с SHA-256 пользовательского промпта. Это удобно показывать как «инженерный
debug-режим».

---

## 5. UI: нижний фронт, пять вкладок

```bash
uv run python -m src.ui
```

Открыть <http://localhost:8501>. Показать по вкладкам:

| Вкладка | Что показать |
|---|---|
| 1. Требования (DZ2) | загрузить `input/registration-requirements.md`, увидеть счётчики файлов/требований, отредактировать текст, выбрать тип и число кейсов |
| 2. Промпт (DZ7) | собранный промпт с BR-N, правка применяется только к выбранному файлу |
| 3. Генерация (DZ2) | чекбокс «Продолжать при ошибке Ollama», кнопка «Сгенерировать», expander с rendered-текстом и скачиванием |
| 4. Метрики (DZ8) | кнопки «Пересчитать метрики» и «Построить графики», строка «Веса overall», таблица по сценариям |
| 5. Отчёт | кнопка «Сформировать отчёт», скачивание `report.md` и `quality-metrics.json`, предпросмотр Markdown |

Отдельно показать сайдбар:

- подключение к Ollama и кнопка «Загрузить модели» (сетевых запросов при старте нет);
- четыре поля весов `overall` с подсказкой канонической строки;
- ошибка при сумме ≠ 1.0 и отказ от генерации;
- кнопка «Очистить состояние» — состояние сбрасывается, файлы в `output/` остаются.

Фраза для защиты: «UI не дублирует бизнес-логику: он вызывает те же
`src/workflow/stages.py`, что и CLI. Веса задаются в сайдбаре и попадают в те же
артефакты».

---

## 6. Надёжность: что показать на вопросы

| Вопрос | Демо-ответ |
|---|---|
| Что будет с битым JSON? | `generation.json` с отсутствующим `filename` → код `2`, сообщение `generation.json: неверный формат артефакта — scenarios[0].filename: обязательное поле отсутствует`, без traceback |
| Совместимость старых файлов? | `examples/live-run/` не имеет `pipeline_version` и `quality_weights`, читается как legacy |
| Почему не `jsonschema`? | pydantic-модели в `src/contracts.py` уже описывают оба файла, новой зависимости нет |
| Почему не LLM-судья? | метрики детерминированы и воспроизводимы; модель только генерирует |
| Почему `clarity` не пересмотрена? | один разметчик, 10 уникальных фрагментов, kappa `n/a`; инструменты готовы, триггер — два human-разметчика |
| Где веса? | CLI → env → defaults; UI задаёт через сайдбар; `--assess` берёт из снимка |

Проверка клиентского кода возврата:

```bash
python -m src.workflow --assess --output-dir output/empty-dir
# код 2: сначала запустите generate
```

---

## 7. Что сказать в конце

1. Проект самодостаточен: нет path-зависимости от ДЗ, всё нужное вендорировано.
2. Кли и UI не расходятся: общие стадии и общие контракты.
3. Результаты воспроизводимы: legacy-файлы читаются, веса хранятся в артефактах.
4. Всё, что требовало данных (`clarity`, readability tolerance), отложено с
   зафиксированным триггером, а не «забыто».

Итог одной фразой:

> Получился рабочий стенд генерации и оценки тестовой документации с двумя
> фронтами, честными артефактами и прозрачными ограничениями метрик.
