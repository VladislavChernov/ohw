# Tasks: генератор промптов + подключение к Dz2

Статус: `[x]` — код и проверка выполнены; `[ ]` — ожидает Docker/Ollama.

## 1. Dz7: движок шаблонов

- [x] 1.1 Переименовать `templates/testcase.txt` → `templates/testcases.txt`;
      привести имена файлов к значениям `ArtifactType` Dz2
- [x] 1.2 `strip_template_comments()` + `render_template()` (regex, не
      `str.format`; литеральный `{"cases": [...]}` остаётся как есть)
- [x] 1.3 `load_template(type, dir)` с явной ошибкой на отсутствующий файл
- [x] 1.4 `build_prompt()`: `{url}`, `{count}`, `{count_noun}`, `{positive}`,
      `{negative}`, `{requirement_rule}`, `{content}`; удаление строки
      `Target site` без URL; ошибка на неизвестный плейсхолдер
- [x] 1.5 Убрать захардкоженные `fill_*_prompt()` и битую кавычку в JSON-примере
- [x] 1.6 Обновить `templates/coveragematrix.txt`: раздел
      `## Матрица покрытия требований` + пример таблицы (требование Dz2-линтера)
- [x] 1.7 Обновить `templates/testcases.txt`: `{requirement_rule}` вместо
      безусловного правила

## 2. Dz7: CLI и конфиг

- [x] 2.1 `--requirements` / `--requirements-file` (взаимоисключающие)
- [x] 2.2 `--input-dir` с дефолтом `../dz2/docs`; имя файла резолвится
      во входной директории основного проекта
- [x] 2.3 `--type` (алиас `testcase` → `testcases`), `--all-types`, `--count`
- [x] 2.4 `--output`, `--output-dir`, `--emit-templates`, `--templates-dir`
- [x] 2.5 Читать `config.toml`: `default_count`, `default_type`,
      `templates_dir`, `input_dir`, `output_dir`
- [x] 2.6 Коды выхода: 0 успех, 2 ошибка аргументов/шаблона/файла
- [x] 2.7 Портативные относительные пути по умолчанию (без `D:\...`)

## 3. Dz2: шаблоны вместо хардкода

- [x] 3.1 `prompt.py`: `resolve_prompt_dir()`, `load_prompt_template()`,
      `render_template()`, `strip_template_comments()`, `build_user_prompt()`
- [x] 3.2 `PromptError`; отсутствие каталога/файла/неизвестный плейсхолдер —
      явная ошибка, без fallback
- [x] 3.3 Восстановить `ArtifactHandler` (модуль должен импортироваться)
- [x] 3.4 Все пять типов собирают user-prompt из `<type>.txt`
- [x] 3.5 Добавить `dz2/prompts/{testcases,checklist,testplan,qareport,coveragematrix}.txt`
- [x] 3.6 `--prompt-dir` + `[paths] prompt_dir` + `DZ2_PROMPT_DIR`
- [x] 3.7 Dockerfile: `COPY prompts /app/prompts`,
      `ENV DZ2_PROMPT_DIR=/app/prompts`; compose: монтирование `./prompts`

## 4. Dz2: пятый тип и валидация

- [x] 4.1 `ArtifactType.QAREPORT`, `ArtifactType.COVERAGEMATRIX`
- [x] 4.2 Алиасы front-matter: `testcase(s)`, `qa_report`, `qa-report`,
      `coverage_matrix`, `coverage`, `traceability`
- [x] 4.3 `QAREPORT_SECTIONS`, `COVERAGE_MATRIX_SECTIONS`
- [x] 4.4 `validate_markdown(require_table=True)` для матрицы
- [x] 4.5 `renderer._DOCUMENT_TITLES`: QA-отчёт, Матрица покрытия

## 5. Тесты и проверка

- [x] 5.1 Dz7: `tests/test_generator.py` (unittest) — шаблоны, подстановка,
      URL, BR-правило, CLI, `--emit-templates` (23 теста, OK)
- [x] 5.2 Dz2: `tests/test_prompts.py` — подстановка, литеральный JSON,
      отсутствие шаблона, неизвестный плейсхолдер, env-переменная
- [x] 5.3 Dz2: registry/renderer/front-matter/валидатор на 5 типов
- [x] 5.4 Dz2: сквозной тест `--prompt-dir` (sentinel виден в промпте)
- [x] 5.5 Прогнать `pytest -m "not integration"`, `ruff`, `mypy` в Docker
      (153 passed; `mypy` и `ruff` чисто)
- [x] 5.6 Запустить e2e: Dz7 → 5 промптов → Dz2 + Ollama → артефакты
      (10 промптов, 2026-10-31, см. `docs/prompt-quality.md`)

## 6. Пресеты, `{extra}` и документация нововведений

- [x] 6.1 `presets/*.toml` (6 шт.): `id`, `label`, `types`, `text`
- [x] 6.2 `Preset`, `load_presets`, `select_extras`, `_extra_block`
- [x] 6.3 `build_prompt(..., extras)`; ошибка при отсутствии `{extra}`
- [x] 6.4 `--preset`, `--presets-dir`, `--list-presets`, `--emit-presets`
- [x] 6.5 `{extra}` во всех пяти шаблонах; `presets_dir` в конфиге
- [x] 6.6 копирование в `dz2/prompts` и `dz2/presets` (байт-в-байт, сверка тестами)
- [x] 6.7 `dz7/README.md`: пресеты, `{extra}`, `--emit-presets`, рабочее место
- [x] 6.8 `dz7/docs/workbench.md`: вкладки, чат, рубрика, границы
- [x] 6.9 `dz7/docs/prompt-quality.md`: методика под `docs/` и пресеты
- [x] 6.10 дельта спеки в `dz7/openspec/changes/prompt-generator/specs`
- [x] 6.11 заполнить таблицы качества после e2e
- [x] 6.12 анти-выдумывание: guard в `_extra_block` (Dz2 и Dz7), уточнённый
      `boundary-values.toml`, тесты-ассерты в обоих наборах
- [x] 6.13 честность `qareport`/`coveragematrix`: не выдумывать прогоны,
      дефекты и TC-id, если их нет во входных данных
- [x] 6.14 повторный e2e после 6.12–6.13 и заполнение таблиц

## 7. Внешняя проверка

- [x] 7.1 `docker compose up ui` + прогон пяти типов в рабочем месте
      (автоматизирован `test_all_five_types_are_generated_and_saved`)

## 6. Документация и оценка качества

- [x] 6.1 `dz7/README.md`: ТЗ + реальные инструкции локального запуска
- [x] 6.2 `dz2/README.md`: внешние промпты, 5 типов, `--prompt-dir`
- [x] 6.3 `dz7/docs/prompt-quality.md`: rubric по 4 критериям + результаты
      (таблица заполняется после e2e)
- [x] 6.4 Добавить `dz7` в индекс `ohw/README.md`
