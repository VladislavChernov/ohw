# Tasks: внешние промпты и новые артефакты

Статус: `[x]` — код и проверка выполнены; `[ ]` — ожидает Docker/Ollama.

## 1. Шаблоны и рендеринг

- [x] 1.1 `prompt.py`: `resolve_prompt_dir`, `load_prompt_template`,
      `strip_template_comments`, `render_template`, `build_user_prompt`
- [x] 1.2 `PromptError` и отсутствие молчаливого fallback
- [x] 1.3 Подстановка regex, литеральный `{"cases": [...]}` не ломается
- [x] 1.4 Удаление строки `Target site` без URL; `{requirement_rule}` только
      при `BR-N`
- [x] 1.5 `dz2/prompts/{testcases,checklist,testplan,qareport,coveragematrix}.txt`

## 2. Обработчики и типы

- [x] 2.1 Восстановить `ArtifactHandler`; `handlers.py` импортируется
- [x] 2.2 Все пять типов читают `<type>.txt`
- [x] 2.3 `ArtifactType.QAREPORT`, `ArtifactType.COVERAGEMATRIX`
- [x] 2.4 Алиасы front-matter (`testcase`, `qa_report`, `coverage_matrix`,
      `coverage`, `traceability` и т.д.)
- [x] 2.5 `QAREPORT_SECTIONS`, `COVERAGE_MATRIX_SECTIONS`
- [x] 2.6 `validate_markdown(require_table=True)`
- [x] 2.7 `renderer._DOCUMENT_TITLES` для QA-отчёта и матрицы

## 3. Конфигурация и поставка

- [x] 3.1 `--prompt-dir`, `[paths] prompt_dir`, `DZ2_PROMPT_DIR`
- [x] 3.2 Dockerfile: `COPY prompts`, `ENV DZ2_PROMPT_DIR`
- [x] 3.3 compose: монтирование `./prompts`
- [x] 3.4 `ai-testgen.toml` и README обновлены

## 4. Тесты

- [x] 4.1 `test_prompts.py`: подстановка, JSON-скобки, URL, BR-правило,
      отсутствие каталога/файла, неизвестный плейсхолдер, env
- [x] 4.2 `test_handlers.py`: все 5 типов, sentinel-шаблон
- [x] 4.3 `test_input_doc.py`: алиасы типов
- [x] 4.4 `test_validator_md.py`: таблица матрицы, разделы QA-отчёта
- [x] 4.5 `test_renderer.py`: заголовки новых типов
- [x] 4.6 `test_cli_artifacts.py`: батч из 5 типов, `--prompt-dir`,
      exit 2 без вызова LLM

## 5. Проверка

- [x] 5.1 `ruff check src tests` (из каталога `dz2`)
- [x] 5.2 Dz7: `python3 -m unittest discover -s tests`
- [ ] 5.3 `pytest -m "not integration"` и `mypy src tests` — только в
      Docker/dev-container (на хосте нет рабочего Python с pydantic)
- [ ] 5.4 e2e: Dz7 → промпты → Dz2 + Ollama → `dz2/output`
- [ ] 5.5 Заполнить `dz7/docs/prompt-quality.md`

## 6. Связанный генератор

- [x] 6.1 `dz7/generator.py`: шаблоны вместо хардкода, исправлен JSON-пример
- [x] 6.2 Dz7 читает требования из `dz2/input` по умолчанию
- [x] 6.3 `--emit-templates`, `--all-types`, `--config`, коды выхода
- [x] 6.4 `dz7/tests/test_generator.py` (21 тест, unittest)
