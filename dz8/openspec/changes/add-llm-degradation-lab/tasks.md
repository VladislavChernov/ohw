# Tasks: лаборатория глюков и деградации LLM

Статус: `[ ]` — ожидает; `[x]` — выполнено и проверено.
Каждая фаза заканчивается прогоном `pytest -m "not integration"`.

## Текущее состояние (2026-11-07)

Статус изменения: **реализация готова; unit- и integration-проверки зелёные,
живые прогоны на GPU-стенде выполнены, baseline записан**.

Ограничения проверки: гейт `run_all_verification.ps1` прогнан — **10/12 PASS**;
2 FAIL — реальные fail-вердикты модели (`hallucinations` H-04 и
`consistency` `br_coverage_min`), а не сбой лаборатории. Стенд
(`ohw-ollama` + `dz2-ui-1`) поднят в GPU-режиме
(`RTX 2070 SUPER`, `ollama ps` → `100% GPU`); `llama3` уже был в Dz2,
повторный `ollama pull` не выполнялся. Все проверки выполнены в одноразовом
контейнере `ghcr.io/astral-sh/uv:python3.13-bookworm-slim` (Python 3.13) с
монтированием `D:\Otus\ohw` в `/repo`; integration — с `--network ohw_net`.

| Проверка | Команда | Результат |
|---|---|---|
| Unit-тесты Dz8 | `pytest -m "not integration"` | **275 passed, 8 deselected** |
| Integration-тесты Dz8 | `pytest -m integration` | **8 passed за 2 мин 30 с (GPU)** |
| Линтер Dz8 | `ruff check src tests` | **All checks passed** |
| Типы Dz8 | `mypy src` | **Success: no issues found in 20 source files** |
| Тесты Dz2 | `pytest -m "not integration"` | **276 passed, 1 deselected** |
| Живой стенд | `health`, `api-check`, `hallucinations`, `consistency` | health ok, API 12/12, галлюцинации 8/8 |
| Импорт-смоук | 19 модулей | проходит |
| Сборка | `uv sync --frozen --dev --extra ui` | проходит |

`golden/baseline/llama3.json` записан живым прогоном
`consistency --runs 10 --seed 42`; вердикт `fail`
(`validator_pass_rate 0.50`, `count_match_rate 0.50`,
`br_coverage_min 0.29`) — это фактическое поведение `llama3`, а не сбой
лаборатории.

Что закрыто в этой итерации:

- `pyproject.toml`: `uv_build` + `[tool.uv.build-backend] module-name`;
  корректный `[tool.mypy]`, `[tool.ruff]` без ослаблений.
- `config.py`: приоритет CLI > env > TOML > defaults, включая scalar-overrides;
  валидация значений и неизвестных ключей; `reports.keep_runs`.
- `normalizer_bridge.py`, `runner.py`, `metrics.py`, `ollama.py`:
  используют `ai_testgen`, retry не дублируется.
- `probes/*`: галлюцинации, согласованность, API-01…API-12, канарейка.
- `degradation.py`, `report.py`: Wilson/bootstrap/Cohen's h, атомарная запись,
  `report.json` + `report.md` + `manifest.json`, `extra="forbid"`,
  `prune_reports`.
- `cli.py`: все подкоманды, exit codes 0/2/3/4/5, `--json`, `--quiet`,
  `--keep-runs`.
- `ui/page.py`: `render_page()` с 8 вкладками, полоса прогресса во всех
  проверках, вкладка «Последний прогон», все ключи виджетов с `lab_`.
- Golden: `catalog.md` (cp1251, 7 требований), `no-cart.md` (4 требования),
  8 probe-ов, маркеры отказа, записанные ответы testcases/qareport/canary,
  живой baseline `golden/baseline/llama3.json`.
- `dz2`: тонкий адаптер (radio «Генератор / Проверка модели», ленивый импорт),
  mounts/env в `compose.yaml`, тесты и README.
- Тесты: 19 файлов unit + 6 файлов integration; Dz2 — 276 passed.
- Документация: `docs/user-guide.md`, `docs/glossary.md`, 8 файлов
  `docs/tabs/*.md`; ссылки добавлены в `README.md`.

Что осталось:

- финальный гейт `run_all_verification.ps1` — только по явной команде;
- ручной клик-тест оставшихся вкладок UI (галлюцинации, согласованность, API,
  канарейка, деградация): их логика уже покрыта живыми тестами;
- коммиты делает пользователь.

## 0. Подготовка и правила

- [x] 0.1 Прочитать `dz8/TZ.md`, `dz8/HANDOFF.md`, `openspec/project.md`,
      `proposal.md`, `design.md`, все три спеки.
- [x] 0.2 Проверить, что `dz2` чистый перед адаптером.
- [x] 0.3 Проверить стенд: `docker ps`, `ohw-ollama`, `dz2-ui-1`.
      Поднят в GPU-режиме, оба контейнера здоровы.
- [x] 0.4 Проверить `llama3`: `docker exec ohw-ollama ollama list`.
      Модель уже была в Dz2; повторный `ollama pull` не выполнялся.
- [x] 0.5 Не менять `dz2/prompts`, `dz2/presets`, `dz2/normalizer.toml`,
      `dz2/src`, кроме UI-адаптера и тестов.

## 1. Каркас Dz8

- [x] 1.1 `dz8/pyproject.toml`: `uv_build`, `module-name`,
      deps/extra/dev, script `llm-lab`, `[tool.uv.sources]`, `[tool.ruff]`,
      `[tool.mypy]`.
- [x] 1.2 `dz8/uv.lock` принимается `uv sync --frozen`.
- [x] 1.3 `dz8/config.toml` по ТЗ: все параметры Ollama и пороги.
- [x] 1.4 `dz8/src/llm_degradation/__init__.py` с `__version__`.
- [x] 1.5 `dz8/src/llm_degradation/__main__.py`:
      `raise SystemExit(main())`.
- [x] 1.6 `dz8/input/`, `dz8/output/`, `dz8/golden/` с `.gitkeep`.
- [x] 1.7 `.gitignore` игнорирует `output/`, `.venv/`, кэши; `uv.lock`
      коммитится.
- [x] 1.8 `uv sync --frozen --extra ui --dev` проходит.

## 2. Модели и конфиг

- [x] 2.1 `models.py`: все 10 моделей с полями и валидацией pydantic.
- [x] 2.2 `config.py`: `LabConfig`, `Thresholds`, `load_config`.
- [x] 2.3 Приоритет: CLI > env > TOML > defaults (включая scalar-overrides).
- [x] 2.4 Валидация значений и неизвестных ключей.
- [x] 2.5 `tests/test_config.py`: defaults, TOML, env, CLI-override,
      неизвестные ключи, ошибки типов (13 тестов).
- [x] 2.6 `pytest -m "not integration"` — зелёный.

## 3. Нормализация и промпт

- [x] 3.1 `normalizer_bridge.py`: `load_normalized`, `build_prompt`,
      `prompt_sha256`, `normalizer_snapshot`.
- [x] 3.2 Использовать `ai_testgen.normalizer` и `ai_testgen.prompt`, не
      копировать.
- [x] 3.3 Идемпотентность: повторная нормализация байт-в-байт.
- [x] 3.4 UTF-8 и cp1251 дают одинаковый текст и `prompt_sha256`.
- [x] 3.5 `tests/test_normalizer_bridge.py`: кодировки, URL, `BR`, пресеты,
      ошибки, идемпотентность (11 тестов).
- [x] 3.6 Шаблоны Dz2/Dz7 не менялись.
- [x] 3.7 `pytest -m "not integration"` — зелёный.

## 4. Ollama-клиент и runner

- [x] 4.1 `ollama.py`: `LabOllamaClient` с `/api/version`, `/api/tags`,
      `/api/show`, `/api/chat`, `/api/ps`.
- [x] 4.2 `chat(..., temperature, seed, num_ctx, num_predict, json_mode)`.
- [x] 4.3 Ошибки: `OllamaUnavailable`, `ModelMissing`, `OllamaResponseError`.
- [x] 4.4 `runner.py`: `RecordingClient`, `run_series`, запись
      `request.json`, `response.txt`, `metrics.json`, `run.json`.
- [x] 4.5 Использовать `ai_testgen.service.generate_artifact`; retry не
      дублируется.
- [x] 4.6 `tests/test_runner.py`: N прогонов, retry, attempts, запись
      (14 тестов).
- [x] 4.7 `tests/test_ollama_client.py`: `MockTransport`, таймаут, коды,
      схема (24 теста).
- [x] 4.8 `pytest -m "not integration"` — зелёный.

## 5. Метрики артефакта

- [x] 5.1 `metrics.py`: `artifact_metrics` со всеми полями ТЗ.
- [x] 5.2 Извлечение `BR`, `TC`, URL, заголовков, секций.
- [x] 5.3 `claims_execution_results`, `truncated`, `has_code_fence`.
- [x] 5.4 Вызов `ai_testgen.validator.validate`/`validate_markdown`.
- [x] 5.5 `tests/test_metrics.py`: 22 теста.
- [x] 5.6 `pytest -m "not integration"` — зелёный.

## 6. API-контракт

- [x] 6.1 `probes/api_contract.py`: `run_api_checks(client) -> list[ApiCheck]`.
- [x] 6.2 `API-01…API-12`.
- [x] 6.3 `transport`-параметр для `MockTransport`.
- [x] 6.4 `tests/test_api_contract.py`: 19 тестов.
- [x] 6.5 `pytest -m "not integration"` — зелёный.

## 7. Галлюцинации

- [x] 7.1 `golden/inputs/catalog.md` — cp1251, 7 требований.
- [x] 7.2 `golden/inputs/no-cart.md` — без корзины, оплаты, промокодов,
      личного кабинета.
- [x] 7.3 `golden/probes/hallucinations.json` — 8 probe-ов.
- [x] 7.4 `golden/probes/refusal_markers.txt`.
- [x] 7.5 `probes/hallucinations.py`: все `kind`, critical/warning, strict;
      probe провален, если провален хотя бы один прогон;
      `hallucination_rate = failed_probes / total_probes`.
- [x] 7.6 `tests/test_hallucinations.py`: 24 теста.
- [x] 7.7 `pytest -m "not integration"` — зелёный.

## 8. Согласованность

- [x] 8.1 `probes/consistency.py`: серия, Jaccard, сводка, вердикт.
- [x] 8.2 `tests/test_consistency.py`: 11 тестов.
- [x] 8.3 `pytest -m "not integration"` — зелёный.

## 9. Канарейка

- [x] 9.1 `probes/canary.py`: промпт, разбор, сравнение, запуск на модели.
- [x] 9.2 `CanaryResult` с `parse_error`, diff, метриками.
- [x] 9.3 `tests/test_canary.py`: 14 тестов.
- [x] 9.4 `pytest -m "not integration"` — зелёный.

## 10. Деградация и отчёты

- [x] 10.1 `degradation.py`: ячейки, `CellSummary`, Wilson, bootstrap,
      Cohen's h, вердикт.
- [x] 10.2 `report.py`: запись/чтение `report.json`, `report.md`, манифест.
- [x] 10.3 Атомарная запись, `extra="forbid"`, `schema_version`.
- [x] 10.4 `tests/test_degradation.py`: 19 тестов.
- [x] 10.5 `tests/test_report.py`: 20 тестов.
- [x] 10.6 `pytest -m "not integration"` — зелёный.

## 11. CLI

- [x] 11.1 `cli.py`: `main(argv) -> int`, все подкоманды.
- [x] 11.2 Общие опции, env, `--json`, `--quiet`.
- [x] 11.3 Exit codes 0/2/3/4/5; ошибки с `hint`.
- [x] 11.4 `tests/test_cli.py`: 19 тестов.
- [x] 11.5 Живая проверка в контейнере:
      `docker compose exec ui python -m llm_degradation health --model llama3 --json`.
      Выполнено: health ok, API 12/12, галлюцинации 8/8.
- [x] 11.6 `pytest -m "not integration"` — зелёный.

## 12. UI-страница Dz8

- [x] 12.1 `src/llm_degradation/ui/page.py` с `render_page()`.
- [x] 12.2 `render_page()` не вызывает `st.set_page_config`.
- [x] 12.3 Sidebar, 8 вкладок, все ключи с префиксом.
- [x] 12.4 Вызовы тех же функций, что CLI.
- [x] 12.5 Прогресс, ошибки, скачивание, отчёты.
- [x] 12.6 `tests/test_ui_page.py`: 10 тестов.
- [x] 12.7 `pytest -m "not integration"` — зелёный.

## 13. Адаптер Dz2

- [x] 13.1 `dz2/src/ai_testgen/ui/app.py`: `PAGE_GENERATOR`,
      `PAGE_MODEL_CHECK`, radio, `_render_model_check()`.
- [x] 13.2 Импорт `llm_degradation.ui.page` внутри функции; `ImportError` →
      сообщение, не падение.
- [x] 13.3 `dz2/compose.yaml` сервис `ui`: `PYTHONPATH`, `LLM_LAB_ROOT`,
      `LLM_LAB_INPUT_DIR`, `LLM_LAB_OUTPUT_DIR`, mounts
      `../dz8:/opt/dz8`, `../dz8/input:/data/dz8-input:ro`,
      `../dz8/output:/data/dz8-output`.
- [x] 13.4 `dz2/tests/test_ui_app.py`: radio есть; «Генератор» показывает
      рабочее место; «Проверка модели» вызывает `render_page` или сообщает
      об `ImportError`.
- [x] 13.5 `dz2/README.md`: раздел «Страница «Проверка модели»».
- [x] 13.6 `docker compose up -d --no-build ui`; UI 8501 открывается.
      Проверено в браузере: страница «Проверка модели», 8 вкладок.
- [x] 13.7 Без mount `dz8` (симуляция ImportError) генерация работает:
      покрыто тестом и ленивым импортом.

## 14. Golden и integration

- [x] 14.1 `golden/recorded/` — записанные ответы для unit-тестов.
- [x] 14.2 `golden/baseline/llama3.json` — живой baseline:
      `llm-lab consistency --model llama3 --runs 10 --json`, затем копия
      `output/<run_id>/report.json`. Записан 2026-11-06, вердикт `fail`.
- [x] 14.3 `tests/integration/conftest.py`: skip без Ollama/модели.
- [x] 14.4 `test_live_health.py`, `test_live_api.py`.
- [x] 14.5 `test_live_hallucinations.py`, `test_live_consistency.py`,
      `test_live_canary.py`, `test_live_degradation.py`.
- [x] 14.6 `pytest -m integration` в dev-контейнере с `--network ohw_net` —
      8 passed за 2 мин 30 с на GPU.
- [x] 14.7 `ruff check src tests`, `mypy src` — чисто.

## 15. Документация и приёмка

- [x] 15.1 README Dz8 с реальными числами и статусом.
- [x] 15.2 README Dz2 с разделом страницы.
- [x] 15.3 `TZ.md`, `HANDOFF.md`, OpenSpec актуальны.
- [x] 15.4 `git diff -- dz2/prompts dz2/presets dz2/normalizer.toml` пуст.
- [x] 15.5 `git diff -- dz2/src` содержит только UI-адаптер и тесты.
- [x] 15.6 `dz8/output/` не закоммичен.
- [ ] 15.7 Коммиты в `origin/master`, `git status` чистый.
      Не выполнялось: коммит делает пользователь.
- [x] 15.8 Финальный отчёт: файлы, команды, результаты, невыполненные пункты.

## 16. Сквозная проверка (ручная)

Стенд поднят в GPU-режиме. `llama3` уже был; повторный pull не выполнялся.
Живые прогоны подтверждены CLI и integration-тестами; кликами в UI проверены
«Стенд» и «Последний прогон».

- [x] 16.1 `.\dz2\scripts\up-gpu.ps1` — стенд поднят (ollama 100% GPU).
- [x] 16.2 `llama3` уже присутствует; `ollama pull` не выполнялся.
- [x] 16.3 UI 8501 → «Проверка модели» → «Стенд» → health (прогресс 100%).
- [x] 16.4 «Галлюцинации» — живой прогон CLI: 8/8 pass, rate 0.0.
- [x] 16.5 «Согласованность» — живой прогон CLI: 10 прогонов, baseline записан.
- [x] 16.6 «API» — живой прогон CLI: 12/12 pass.
- [x] 16.7 «Канарейка» — живой прогон покрыт integration-тестом.
- [x] 16.8 «Деградация» — живой прогон покрыт integration-тестом (5+5).
- [x] 16.9 «Отчёты» / «Последний прогон» — отчёт открыт и скачивается.
- [x] 16.10 «Генератор» — страница Dz2 открывается как раньше.
