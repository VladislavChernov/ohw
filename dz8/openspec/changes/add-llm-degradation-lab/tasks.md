# Tasks: лаборатория глюков и деградации LLM

Статус: `[ ]` — ожидает; `[x]` — выполнено и проверено.
Код пишет локальная модель. Каждая фаза заканчивается прогоном
`pytest -m "not integration"`.

## 0. Подготовка и правила

- [ ] 0.1 Прочитать `dz8/TZ.md`, `dz8/HANDOFF.md`, `openspec/project.md`,
      `proposal.md`, `design.md`, все три спеки.
- [ ] 0.2 Проверить, что `dz2` чистый: `git -C D:\Otus\ohw status --short`.
- [ ] 0.3 Проверить стенд: `docker ps`, `ohw-ollama`, `dz2-ui-1`.
- [ ] 0.4 Проверить `llama3`: `docker exec ohw-ollama ollama list`.
      Если нет — `docker exec ohw-ollama ollama pull llama3`.
- [ ] 0.5 Не менять `dz2/prompts`, `dz2/presets`, `dz2/normalizer.toml`,
      `dz2/src`, кроме UI-адаптера и тестов.

## 1. Каркас Dz8

- [ ] 1.1 `dz8/pyproject.toml`:
      - `name = "llm-degradation-lab"`;
      - `requires-python = ">=3.13"`;
      - deps: `httpx`, `pydantic`, `ai-testcase-generator` (path `../dz2`);
      - extra `ui`: `streamlit`;
      - dev group: `pytest`, `pytest-mock`, `ruff`, `mypy`, `streamlit`;
      - script `llm-lab = "llm_degradation.cli:main"`;
      - `[tool.uv.sources] ai-testcase-generator = { path = "../dz2", editable = true }`;
      - `build-system`: `uv_build`, `module-name = "llm_degradation"`;
      - `[tool.pytest.ini_options] markers`: `integration`, `slow`, `ui`;
      - `[tool.ruff]`, `[tool.mypy]` без ослаблений.
- [ ] 1.2 `dz8/uv.lock` сгенерирован в контейнере `ui`:
      `docker compose exec ui sh -lc "cd /opt/dz8 && uv lock"`.
- [ ] 1.3 `dz8/config.toml` по ТЗ, раздел 11.
- [ ] 1.4 `dz8/src/llm_degradation/__init__.py` с `__version__`.
- [ ] 1.5 `dz8/src/llm_degradation/__main__.py`:
      `raise SystemExit(main())`.
- [ ] 1.6 Пустые `dz8/input/`, `dz8/output/`, `dz8/golden/` с `.gitkeep`.
- [ ] 1.7 `.gitignore`: `output/`, `.venv/`, `__pycache__/`, `.pytest_cache/`,
      `.ruff_cache/`, `.mypy_cache/`, `*.tmp`.
- [ ] 1.8 Проверка:
      `docker compose exec -e UV_PROJECT_ENVIRONMENT=/tmp/dz8-venv ui sh -lc "cd /opt/dz8 && uv sync --frozen --extra ui --dev"`
      проходит.

## 2. Модели и конфиг

- [ ] 2.1 `models.py`: `NormalizedInput`, `PromptBundle`, `ChatCall`,
      `RunRecord`, `ArtifactMetrics`, `ProbeResult`, `CanaryResult`,
      `CellSummary`, `ExperimentReport`, `ErrorReport`.
- [ ] 2.2 `config.py`: `LabConfig`, `Thresholds`, `load_config`.
- [ ] 2.3 Приоритет: CLI > env > TOML > defaults.
- [ ] 2.4 Валидация значений и неизвестных ключей.
- [ ] 2.5 `tests/test_config.py`: defaults, TOML, env, CLI-override,
      неизвестные ключи, ошибки типов.
- [ ] 2.6 `pytest -m "not integration"` — зелёный.

## 3. Нормализация и промпт

- [ ] 3.1 `normalizer_bridge.py`: `load_normalized`, `build_prompt`,
      `prompt_sha256`.
- [ ] 3.2 Использовать `ai_testgen.normalizer` и `ai_testgen.prompt`, не
      копировать.
- [ ] 3.3 Идемпотентность: повторная нормализация байт-в-байт.
- [ ] 3.4 UTF-8 и cp1251 дают одинаковый текст и `prompt_sha256`.
- [ ] 3.5 `tests/test_normalizer_bridge.py`: кодировки, URL, `BR`, пресеты,
      ошибки, идемпотентность.
- [ ] 3.6 `tests/test_prompts_sync.py` (если нужно): шаблоны Dz2 совпадают с
      Dz7; не менять их.
- [ ] 3.7 `pytest -m "not integration"` — зелёный.

## 4. Ollama-клиент и runner

- [ ] 4.1 `ollama.py`: `LabOllamaClient` с `/api/version`, `/api/tags`,
      `/api/show`, `/api/chat`, `/api/ps`.
- [ ] 4.2 `chat(..., temperature, seed, num_ctx, num_predict, json_mode)`.
- [ ] 4.3 Ошибки: `OllamaUnavailable`, `ModelMissing`, `OllamaResponseError`.
- [ ] 4.4 `runner.py`: `RecordingClient`, `run_series`, запись
      `request.json`, `response.txt`, `metrics.json`.
- [ ] 4.5 Использовать `ai_testgen.service.generate_artifact`; не дублировать
      retry.
- [ ] 4.6 `tests/test_runner.py`: N прогонов, retry, attempts, запись.
- [ ] 4.7 `tests/test_ollama_client.py`: `MockTransport`, таймаут, коды,
      схема.
- [ ] 4.8 `pytest -m "not integration"` — зелёный.

## 5. Метрики артефакта

- [ ] 5.1 `metrics.py`: `artifact_metrics` со всеми полями ТЗ.
- [ ] 5.2 Извлечение `BR`, `TC`, URL, заголовков, секций.
- [ ] 5.3 `claims_execution_results`, `truncated`, `has_code_fence`.
- [ ] 5.4 Вызов `ai_testgen.validator.validate`/`validate_markdown`.
- [ ] 5.5 `tests/test_metrics.py`:
      - валидный JSON;
      - битый JSON;
      - пустой ответ;
      - огромный ответ;
      - выдуманные BR/TC/URL;
      - fences;
      - truncated;
      - claims;
      - Markdown-секции;
      - мусорные URL.
- [ ] 5.6 `pytest -m "not integration"` — зелёный.

## 6. API-контракт

- [ ] 6.1 `probes/api_contract.py`: `run_api_checks(client) -> list[ApiCheck]`.
- [ ] 6.2 `API-01…API-12` из ТЗ.
- [ ] 6.3 `transport`-параметр для `MockTransport`.
- [ ] 6.4 `tests/test_api_contract.py`: все проверки, коды, схемы, таймаут,
      параллельность (mock).
- [ ] 6.5 `pytest -m "not integration"` — зелёный.

## 7. Галлюцинации

- [ ] 7.1 `golden/inputs/catalog.md` — копия `dz2/input/e2e/requirements.md`.
- [ ] 7.2 `golden/inputs/no-cart.md` — синтетический документ без корзины,
      оплаты, промокодов, личного кабинета.
- [ ] 7.3 `golden/probes/hallucinations.json` — 8 probe-ов; у каждого
      `input`, `kind`, `critical`, `checks` и либо `prompt`, либо
      `artifact_type`.
- [ ] 7.4 `golden/probes/refusal_markers.txt`.
- [ ] 7.5 `probes/hallucinations.py`: все `kind`, critical/warning, strict;
      probe провален, если провален хотя бы один из `--runs` (по умолчанию 3);
      `hallucination_rate = failed_probes / total_probes`.
- [ ] 7.6 `tests/test_hallucinations.py` на записанных ответах, включая
      провал в одном из нескольких прогонов.
- [ ] 7.7 `pytest -m "not integration"` — зелёный.

## 8. Согласованность

- [ ] 8.1 `probes/consistency.py`: серия, Jaccard, сводка, вердикт.
- [ ] 8.2 `tests/test_consistency.py`: пустые множества, пары, пороги.
- [ ] 8.3 `pytest -m "not integration"` — зелёный.

## 9. Канарейка

- [ ] 9.1 `probes/canary.py`: промпт, разбор, сравнение.
- [ ] 9.2 `CanaryResult` с `parse_error`, diff, метриками.
- [ ] 9.3 `tests/test_canary.py`: точное совпадение, лишний/пропавший ID,
      другой URL, переписанный текст, parse_error.
- [ ] 9.4 `pytest -m "not integration"` — зелёный.

## 10. Деградация и отчёты

- [ ] 10.1 `degradation.py`: ячейки, `CellSummary`, Wilson, bootstrap,
      Cohen's h, вердикт.
- [ ] 10.2 `report.py`: запись/чтение `report.json`, `report.md`, манифест.
- [ ] 10.3 Атомарная запись, `extra="forbid"`, `schema_version`.
- [ ] 10.4 `tests/test_degradation.py`: Wilson крайние случаи, bootstrap seed,
      сравнение, вердикты, `insufficient_data`.
- [ ] 10.5 `tests/test_report.py`: схемы, запись/чтение, MD, каталог.
- [ ] 10.6 `pytest -m "not integration"` — зелёный.

## 11. CLI

- [ ] 11.1 `cli.py`: `main(argv) -> int`, подкоманды `health`, `normalize`,
      `api-check`, `hallucinations`, `consistency`, `canary`, `degradation`,
      `report`.
- [ ] 11.2 Общие опции, env, `--json`, `--quiet`.
- [ ] 11.3 Exit codes 0/2/3/4/5; ошибки с `hint`.
- [ ] 11.4 `tests/test_cli.py`: парсинг, коды, ровно один JSON, stderr.
- [ ] 11.5 Проверка в контейнере:
      `docker compose exec ui python -m llm_degradation health --model llama3 --json`.
- [ ] 11.6 `pytest -m "not integration"` — зелёный.

## 12. UI-страница Dz8

- [ ] 12.1 `ui/__init__.py`, `ui/page.py` с `render_page()`.
- [ ] 12.2 `render_page()` не вызывает `st.set_page_config`.
- [ ] 12.3 Sidebar, 7 вкладок, все ключи с `lab_`.
- [ ] 12.4 Вызовы тех же функций, что CLI.
- [ ] 12.5 Прогресс, ошибки, скачивание, отчёты.
- [ ] 12.6 `tests/test_ui_page.py`: AppTest, 7 вкладок, health с моком,
      таблицы.
- [ ] 12.7 `pytest -m "not integration"` — зелёный.

## 13. Адаптер Dz2

- [ ] 13.1 `dz2/src/ai_testgen/ui/app.py`: `PAGE_GENERATION`,
      `PAGE_MODEL_CHECK`, radio, `_render_model_check()`.
- [ ] 13.2 Импорт `llm_degradation.ui.page` внутри функции; `ImportError` →
      сообщение + команда, не падение.
- [ ] 13.3 `dz2/compose.yaml` сервис `ui`: `PYTHONPATH`, `LLM_LAB_ROOT`,
      `LLM_LAB_INPUT_DIR`, `LLM_LAB_OUTPUT_DIR`, mounts
      `../dz2:/opt/dz2:ro`, `../dz8:/opt/dz8`,
      `../dz8/input:/data/dz8-input:ro`,
      `../dz8/output:/data/dz8-output`.
- [ ] 13.4 `dz2/tests/test_ui_app.py`: radio есть; «Генерация» — 5 вкладок;
      «Проверка модели» — 7 вкладок или сообщение при ImportError.
- [ ] 13.5 `dz2/README.md`: раздел «Страница проверки модели».
- [ ] 13.6 `docker compose up -d --no-build ui`; UI 8501 открывается.
- [ ] 13.7 Без mount `dz8` (симуляция ImportError) генерация работает.

## 14. Golden и integration

- [ ] 14.1 `golden/recorded/` — реальные ответы для unit-тестов.
- [ ] 14.2 `golden/baseline/llama3.json` — живой baseline, не руками:
      `llm-lab consistency --model llama3 --runs 10 --json`, затем копия
      `output/<run_id>/report.json`.
- [ ] 14.3 `tests/integration/conftest.py`: skip без Ollama/модели.
- [ ] 14.4 `test_live_health.py`, `test_live_api.py`.
- [ ] 14.5 `test_live_hallucinations.py`, `test_live_consistency.py`,
      `test_live_canary.py`, `test_live_degradation.py`.
- [ ] 14.6 `cd /opt/dz8 && pytest -m integration` в `dz2-ui-1` — зелёный или
      skip с причиной.
- [ ] 14.7 `cd /opt/dz8 && ruff check src tests`, `mypy src` — чисто.

## 15. Документация и приёмка

- [ ] 15.1 README Dz8 с реальными числами и командами.
- [ ] 15.2 README Dz2 с разделом страницы.
- [ ] 15.3 `TZ.md`, `HANDOFF.md`, OpenSpec актуальны.
- [ ] 15.4 `git diff -- dz2/prompts dz2/presets dz2/normalizer.toml` пуст.
- [ ] 15.5 `git diff -- dz2/src` содержит только UI-адаптер и тесты.
- [ ] 15.6 `dz8/output/` не закоммичен.
- [ ] 15.7 Коммиты в `origin/normalizer`, `git status` чистый.
- [ ] 15.8 Финальный отчёт: файлы, команды, результаты, невыполненные пункты.

## 16. Сквозная проверка (ручная)

- [ ] 16.1 `.\dz2\scripts\up-gpu.ps1` (или CPU).
- [ ] 16.2 `docker exec ohw-ollama ollama pull llama3`.
- [ ] 16.3 UI 8501 → «Проверка модели» → «Стенд» → health.
- [ ] 16.4 «Галлюцинации» → 3 прогона → отчёт.
- [ ] 16.5 «Согласованность» → 10 прогонов → отчёт.
- [ ] 16.6 «API» → проверка → pass.
- [ ] 16.7 «Канарейка» → 1 прогон → diff.
- [ ] 16.8 «Деградация» → baseline `llama3`, candidate `llama3`, 5 прогонов →
      отчёт с CI.
- [ ] 16.9 «Отчёты» → открыть и скачать.
- [ ] 16.10 «Генерация артефактов» → существующий сценарий работает.
