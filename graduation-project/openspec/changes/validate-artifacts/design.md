# Дизайн: валидация артефактов через pydantic

## Контекст

Артефакты читаются в двух местах:

| Файл | Чтение | Модель |
|---|---|---|
| `generation.json` | `src/workflow/stages.py::load_snapshot` | `PipelineData.from_snapshot_dict` |
| `quality-metrics.json` | `src/reporting/report_generator.py::load_pipeline_data` | `PipelineData.from_report_dict` |

Оба загрузчика вызывают `check_pipeline_version`, который проверяет только
версию. Структуру полей никто не проверяет, поэтому `raw["filename"]` — это
потенциальный `KeyError`, а `int(raw.get("attempts", 1))` — потенциальный
`ValueError` с английским сообщением без контекста.

## Слои

```text
JSON на диске
    │  read_json_object: UTF-8 + JSON-объект + имя файла
    ▼
pydantic-документ (src/contracts.py)
    │  SnapshotDocument / ReportDocument
    ▼
PipelineData.from_*_dict: dataclass-ы без ручного int()/list()
```

`src/contracts.py` зависит только от `pydantic` и
`src.generator.models.TestCase`; имена файлов заданы константами модуля.
Обратной зависимости от `src.models` нет, поэтому цикла импортов не возникает.

## Схемы

### `SnapshotDocument`

- `pipeline_version: str | None` — необязателен для legacy;
- метаданные (`input_dir`, `artifact_type`, `model`, `runs`, `count`,
  `temperature`, `normalize`, `presets`, `generated_at`) — со значениями по
  умолчанию;
- `quality_weights: dict[str, Any] | None` — семантику (полнота, знак, сумма)
  проверяет существующий `validate_quality_weights`;
- `scenarios: list[ScenarioSnapshot] | None`;
- `errors: list[str] | None`.

### `ScenarioSnapshot`

Обязательное — только `filename` (непустая строка). Остальные поля имеют
значения по умолчанию, повторяющие прежнее поведение `raw.get(...)`:

- `source_path`, `artifact_type`, `output_file`, `rendered`, `generations`,
  `cases`, `prompt`, `attempts`, `quality`, `requirement_ids`,
  `covered_requirements`, `prompt_sha256`, `runs`, `error`.

`cases` валидируются существующей моделью `TestCase`, поэтому битый тест-кейс
тоже даёт понятную ошибку.

### `ReportDocument` и `ScenarioReport`

Повторяют структуру `quality-metrics.json`. Обязательное поле сценария —
`filename`; `quality`, `requirement_ids`, `cases_with_requirements`,
`case_summaries` допускают `null`, как и раньше. `averages`, `charts`,
`errors`, `total_scenarios` и прочие поля отчёта необязательны: валидный
JSON-объект без части необязательных полей читается, а средние при
необходимости пересчитываются.

## Ошибки

`read_json_object`:

- файл не читается как UTF-8 → `ValueError` с именем файла;
- `JSONDecodeError` → `ValueError` с именем файла, строкой и колонкой;
- JSON верхнего уровня не объект → `ValueError` с именем файла.

`_validate`:

- `ValidationError` → `ValueError`
  `"<файл>: неверный формат артефакта — <loc>: <сообщение>"`;
- `loc` печатается в виде `scenarios[0].filename`;
- показываются первые три поля, остальные сворачиваются в «и ещё N»;
- типовые ошибки pydantic переводятся на русский (`обязательное поле
  отсутствует`, `ожидается строка`, `ожидается число`, ...).

CLI уже ловит `ValueError` и возвращает `2`, поэтому отдельных обработчиков не
требуется.

## Совместимость

- `pipeline_version` не повышается.
- Поля, которые раньше читались через `raw.get(..., default)`, остаются
  необязательными.
- Неизвестные ключи игнорируются (`extra="ignore"`), чтобы будущие
  необязательные поля не ломали текущую версию.
- Явный `null` в скалярных метаданных (`input_dir`, `generated_at`, `model`,
  `normalize`, ...) — ошибка: пайплайн таких значений не пишет. `null`
  допускается только там, где он был допустим раньше: `output_file`, `error`,
  `quality`, `averages`, `charts` и списочные поля.
- Валидируется весь контракт, включая поля, которые текущие фазы не читают
  (`project`, `version`, `total_scenarios`). Артефакт должен быть целостным,
  а не доверенным частично.
- `quality_weights` без ключа → веса по умолчанию; `null` → `ValueError`;
  неполный или нечисловой набор → `ValueError` от
  `validate_quality_weights`. Текст ошибки весов тоже начинается с имени
  файла.
- Pydantic парсит строковые числа и булевы значения в lax-режиме
  (`"false"` → `False`); прежний `bool("false")` давал `True`. Это осознанное
  ужесточение: артефакты пишет сам пайплайн, а не произвольный ввод.
- Экземпляры `examples/live-run/` (legacy без `pipeline_version`) читаются как
  раньше.

## Альтернативы

- **`jsonschema` + отдельный файл схемы.** Дублирует структуру и добавляет
  зависимость без внешнего потребителя; отклонено.
- **Валидировать прямо в `from_*_dict` без отдельного модуля.** Смешивает
  сериализацию и контракт, а также оставляет ручные `int()`/`list()`; отклонено.
- **Строгий режим (`extra="forbid"`).** Ломает чтение файлов, записанных более
  новой версией с необязательными полями; отклонено.
- **Требовать все поля снимка.** Не совместимо с legacy и повышает
  `pipeline_version` без необходимости; отклонено.
