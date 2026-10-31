# Tasks: пресеты, рабочее место и единое ядро

Статус: `[x]` — код и проверка выполнены; `[ ]` — ожидает проверки.

## 1. Источник требований

- [x] 1.1 `Config.input_dir = Path("docs")` и `[paths] input_dir = "docs"`
- [x] 1.2 `ai-testgen.toml`, README, compose (`./docs:/data/docs:ro`)
- [x] 1.3 Dz7: `DEFAULT_DZ2_INPUT_DIR = ../dz2/docs`, `dz7/config.toml`
- [x] 1.4 `test_default_input_dir_is_docs`, `test_default_input_dir_is_dz2_docs`

## 2. Пресеты и `{extra}`

- [x] 2.1 `presets.py`: `Preset`, `load_presets`, `select_extras`,
      `resolve_presets_dir`, `PresetError`
- [x] 2.2 `prompt.py`: `{extra}`, `_extra_block`, ошибка при отсутствии
      плейсхолдера при выбранном пресете
- [x] 2.3 `handlers.UserPromptBuilder` с опциональными extras
- [x] 2.4 Конфиг: `[paths] presets_dir`, `DZ2_PRESETS_DIR`
- [x] 2.5 шести пресетов в `dz7/presets` + синхронизация в `dz2/presets`
- [x] 2.6 Dz7: `Preset`, `load_presets`, `select_extras`, `--preset`,
      `--presets-dir`, `--list-presets`, `--emit-presets`
- [x] 2.7 `{extra}` во всех пяти шаблонах Dz7

## 3. CLI: явный тип и имена файлов

- [x] 3.1 `--type` / `--all-types` (взаимоисключающие)
- [x] 3.2 `parse_artifact_type` для CLI
- [x] 3.3 `<stem>.<type>.md` при явном типе, `<stem>.md` для front-matter
- [x] 3.4 коды выхода 2 для неизвестного типа, пресета и отсутствующего каталога

## 4. Общее ядро

- [x] 4.1 `service.GenerationOptions`, `RetryNotice`, `GeneratedArtifact`,
      `GenerationError`, `generate_artifact`
- [x] 4.2 CLI переведён на ядро, `on_retry` печатает замечания
- [x] 4.3 UI вызывает то же ядро и принимает редактированный промпт

## 5. Рабочее место

- [x] 5.1 `ui/app.py`: пять вкладок, editable требования и промпт
- [x] 5.1a загрузка файлов требований через `st.file_uploader` (`.md`/`.txt`,
      несколько файлов), без поля «каталог»; ошибка на не-UTF-8 без падения
- [x] 5.1b таймаут запроса в боковой панели (CPU-инференс не укладывается
      в 180 с по умолчанию)
- [x] 5.1c подключение к модели: пресеты адреса, список из `GET /api/tags`
      или ручной ввод модели; env-приоритет как в CLI
- [x] 5.2 выбор типа, count, пресетов; пересборка промпта по подписи состава
- [x] 5.3 генерация, просмотр (документ/markdown/промпт), скачивание,
      сохранение в `output/`
- [x] 5.4 чат: `chat_messages`, `pending_revision`, «Применить как новую версию»
      с валидацией
- [x] 5.5 рубрика 1–5 → `output/quality/<type>[-<preset>]-<run-id>.json`
- [x] 5.6 `OllamaClient.chat_messages`, `list_models`
- [x] 5.7 `ui/launcher.py`, entry point `ai-testgen-ui`, extra `ui`
- [x] 5.8 LLM-судья `evaluation.py`: четыре критерия 1–5, отдельный вызов и
      своя рубрика на каждый критерий (общий ответ с примером модель
      копировала: 3/4/4/3 на всех промптах), `temperature=0.0`, `prompt_sha256`,
      `judge_version`, обязательные цитаты с проверкой на вхождение (допускаются
      регистр/пунктуация, до трёх попыток; балл 1 может быть без цитат)
- [x] 5.9 связка «кейсы → матрица»: `{testcases}`, `testcase_index`, кейсы из
      `--all-types` переходят в промпт матрицы, без кейсов — `—`/`Not Covered`
- [x] 5.10 `run_id` в имени оценки и ссылка на сгенерированный артефакт
      (`generated_artifact`: путь, sha256, размер, mtime) в JSON, UI показывает
      и скачивает артефакт, CLI подхватывает уже существующий `<stem>.<type>.md`

## 6. Docker

- [x] 6.1 `COPY presets`, `DZ2_PRESETS_DIR`, монтирование `./presets`
- [x] 6.2 `ARG EXTRAS` в Dockerfile (без cache-mount директив — собирается и
      legacy builder, и BuildKit)
- [x] 6.3 compose-сервис `ui`: extra `ui`, порт 8501, entrypoint
      `ai-testgen-ui`, volumes
- [x] 6.4 сборка `ui`-образа и запуск контейнера проверены (health `ok`,
      AppTest внутри образа видит `docs/` и пресеты)
- [x] 6.5 Dockerfile `FROM ohw-python:3.13` — общий базовый образ из
      `infra/python`, без дублирования python/uv/пользователя
- [x] 6.6 один образ `ohw/dz2:local` на CLI и UI; `app` переиспользует образ
      `ui`, отдельных `ai-testgen-ui:*`/legacy-образов нет
- [x] 6.7 стенд-скрипты `scripts/`: Windows CPU/GPU проверены end-to-end,
      Linux/macOS CPU/GPU-скрипты написаны и проверены `bash -n`, GPU-оверлей из
      `infra/`, ожидание health, `down`

## 7. Тесты

- [x] 7.1 `test_presets.py` (включая байтовую сверку с `dz7/presets`)
- [x] 7.2 `test_cli_artifacts.py`: `--type`, `--all-types`, пресеты, ошибки
- [x] 7.3 `test_ui_app.py` (`AppTest`: загрузка файлов, промпт, подключение,
      генерация, оценка)
- [x] 7.4 `test_config.py`: `presets_dir`, `input_dir = docs`
- [x] 7.5 `test_prompts.py`: байтовая сверка с `dz7/templates`
- [x] 7.6 Dz7: 39 тестов, включая пресеты и `--emit-presets`
- [x] 7.7 `pytest` 207 passed, `mypy` чисто, `ruff` — только `EXE002` на
      смонтированных файлах (Docker)
- [x] 7.8 `test_evaluation.py`: разбор/валидация JSON судьи, `--evaluate`,
      UI-сохранение `source=llm`

## 8. Документация

- [x] 8.1 `dz2/README.md`: работа, пресеты, `--type`/`--all-types`, UI,
      Docker UI, конфиг
- [x] 8.2 `dz7/README.md`: `{extra}`, пресеты, `--emit-presets`, UI
- [x] 8.3 `dz7/docs/workbench.md`
- [x] 8.4 `dz7/docs/prompt-quality.md`: методика под `docs/` и пресеты
- [x] 8.5 OpenSpec: этот бандл + дельта в `dz7/openspec`

## 9. Сквозная проверка

- [x] 9.1 CLI e2e: `scripts/up-gpu.ps1` + `docker compose run --rm app
      --all-types` с общим Ollama (прогон 2026-10-31, 10 промптов)
- [x] 9.2 UI e2e: `scripts/up-gpu.ps1`, вкладки и подключение проверены;
      прогон пяти типов автоматизирован тестом
      `test_all_five_types_are_generated_and_saved` (Streamlit AppTest, тот же
      виджет загрузки, что и в браузере)
- [x] 9.3 заполнить таблицу в `dz7/docs/prompt-quality.md` из rubric-JSON
- [x] 9.4 прогнать стенд-скрипты (`up-gpu.ps1`, `up-cpu.ps1`, `down.ps1`)
      end-to-end на Windows; Linux/macOS-скрипты проверены `bash -n`
