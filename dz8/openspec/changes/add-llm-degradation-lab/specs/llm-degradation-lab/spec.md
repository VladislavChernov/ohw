# Дельта спеки: llm-degradation-lab

## ADDED Requirements

### Requirement: Размещение в рабочем месте Dz2

- Проверки SHALL быть доступны отдельной страницей «Проверка модели» внутри
  существующего UI Dz2 на `http://localhost:8501`.
- В sidebar SHALL быть переключатель страниц «Генерация артефактов» /
  «Проверка модели».
- Выбор «Генерация артефактов» SHALL показывать существующие пять вкладок без
  изменения поведения.
- Выбор «Проверка модели» SHALL вызывать
  `llm_degradation.ui.page.render_page()`.
- `render_page()` SHALL NOT вызывать `st.set_page_config`.
- Все widget keys страницы Dz8 SHALL иметь префикс `lab_`, чтобы не
  пересекаться с Dz2.
- **WHEN** `llm_degradation` не импортируется, UI SHALL показывать сообщение с
  командой проверки mount, а страница «Генерация артефактов» SHALL продолжать
  работать.
- Импорт страницы SHALL выполняться внутри функции, а не на уровне модуля.

#### Scenario: Открытие страницы проверки

- **WHEN** пользователь выбирает «Проверка модели»
- **THEN** отображаются вкладки «Стенд», «Галлюцинации», «Согласованность»,
  «API», «Канарейка», «Деградация», «Последний прогон», «Отчёты»
- **AND** приложение не падает, даже если Ollama недоступна

#### Scenario: Отсутствие Dz8

- **WHEN** каталог `dz8` не смонтирован в контейнер `ui`
- **THEN** страница «Проверка модели» показывает ошибку и команду запуска
- **AND** страница «Генерация артефактов» работает как раньше

### Requirement: Использование общего стенда

- Система SHALL использовать существующий контейнер `ohw-ollama` из
  `infra/compose.yaml`.
- Система SHALL использовать существующий контейнер `dz2-ui-1` из
  `dz2/compose.yaml`.
- Система SHALL NOT создавать второй Ollama, отдельный контейнер Dz8, сервис
  `lab` или порт 8502.
- Модель `llama3` SHALL загружаться в `ohw-ollama` командой
  `docker exec ohw-ollama ollama pull llama3`.
- Система SHALL NOT выполнять `ollama pull` автоматически в тестах или при
  старте UI.
- CLI и тесты SHALL запускаться внутри `dz2-ui-1`; временное venv SHALL
  создаваться в `/tmp`, а не в `/app/.venv`.

#### Scenario: Проверка стенда

- **WHEN** выполнена команда `health`
- **THEN** система проверяет `/api/version`, `/api/tags` и наличие модели
- **AND** при отсутствии модели возвращает код 3 и подсказку
  `docker exec ohw-ollama ollama pull llama3`

### Requirement: Контракт нормализации входа

- Каждый вход SHALL проходить `ai_testgen.normalizer` до сборки промпта.
- Система SHALL сохранять `source_sha256`, `text_sha256`, кодировку,
  заголовок, URL, `BR-N`, настройки нормализатора и предупреждения.
- `prompt_sha256` SHALL считаться как
  `sha256(user_prompt.encode("utf-8")).hexdigest()`.
- Один и тот же документ в UTF-8 и cp1251 SHALL давать байт-в-байт одинаковый
  канонический текст и одинаковый `prompt_sha256`.
- Повторная нормализация канонического текста SHALL быть идемпотентной.
- Система SHALL NOT переписывать требования и SHALL NOT подменять `user`
  после нормализации.
- **WHEN** нормализация не удалась или не идемпотентна
- **THEN** CLI возвращает код 2, UI показывает ошибку, прогон не начинается.

#### Scenario: Кодировки

- **WHEN** один документ подан в UTF-8 и cp1251
- **THEN** `NormalizedInput.text` совпадает байт-в-байт
- **AND** `prompt_sha256` совпадает

#### Scenario: Идемпотентность

- **WHEN** канонический текст нормализуется повторно
- **THEN** результат совпадает с входом байт-в-байт

### Requirement: Проверка галлюцинаций

- Система SHALL поддерживать probe-набор из
  `golden/probes/hallucinations.json`.
- Probe SHALL иметь `id`, `kind`, `input`, `critical`, `checks` и либо
  `prompt`, либо `artifact_type`.
- Система SHALL поддерживать `kind`: `count`, `missing_feature`,
  `missing_url`, `entity`, `traceability`, `execution_claims`, `fake_tc`,
  `numeric`.
- Проверки SHALL быть детерминированными: регулярки, множества ID/URL,
  маркеры отказа, метрики артефакта.
- Система SHALL NOT использовать LLM-судью для вердикта о галлюцинации.
- Система SHALL считать `hallucination_rate` и список `critical_failures`.
- Каждый probe SHALL выполняться `runs` раз (по умолчанию 3); probe SHALL
  считаться проваленным, если провален хотя бы один прогон.
- Probe-ы с `artifact_type` SHALL выполняться с `max_retries=0`, чтобы retry
  не маскировал галлюцинацию; `GenerationError` SHALL считаться провалом
  probe.
- `hallucination_rate` SHALL считаться как
  `failed_probes / total_probes`, а не по прогонам.
- В отчёт SHALL попадать `failed_runs`, `failed_runs_total`, `runs_total`.
- Вердикт SHALL быть `fail`, если есть critical failure или
  `hallucination_rate > threshold`.
- Вердикт SHALL быть `warn`, если `0 < rate <= threshold`.
- Вердикт SHALL быть `pass`, если `rate == 0` и critical failures нет.
- `--strict` SHALL делать все probe-ы critical и порог `0.0`.
- В отчёт SHALL попадать `evidence` до 200 символов и `details`.

#### Scenario: Провал в одном из прогонов

- **WHEN** probe выполнен 3 раза и в одном прогоне модель выдумала `BR-99`
- **THEN** `failed_runs=1`
- **AND** probe считается проваленным
- **AND** вердикт `fail`, если probe critical

#### Scenario: Выдуманный BR

- **WHEN** модель возвращает `requirement: "BR-99"`, которого нет во входе
- **THEN** `br_invented` содержит `BR-99`
- **AND** probe `traceability` падает
- **AND** общий вердикт `fail`

#### Scenario: Вердикт без данных

- **WHEN** QA-отчёт утверждает «все тесты пройдены», а вход не содержит
  результатов прогона
- **THEN** `claims_execution_results=true`
- **AND** probe `execution_claims` падает

### Requirement: Проверка согласованности

- Система SHALL выполнять серию N прогонов одного промпта с одинаковыми
  параметрами.
- Система SHALL считать `pass@1`, `pass@all`, `valid_json_rate`,
  `validator_pass_rate`, `count_match_rate`, `br_coverage_mean`,
  `br_coverage_min`, `tc_jaccard_mean`, `tc_jaccard_min`,
  `heading_jaccard_mean`, `title_jaccard_mean`, `identical_runs`,
  `unique_artifacts`, `self_consistency`.
- Jaccard SHALL быть `|A ∩ B| / |A ∪ B|`; два пустых множества → `1.0`,
  иначе `0.0`.
- Система SHALL сохранять каждый `request.json`, `response.txt`,
  `metrics.json`.
- Вердикт SHALL быть `fail`, если `validator_pass_rate < 0.9`,
  `count_match_rate < 1.0` для testcases, `br_coverage_min < 0.8` или
  `tc_jaccard_mean < 0.4` для testcases.
- `identical_runs == 0` при `temperature=0.0` SHALL быть warning, не fail.
- Пороги SHALL быть в конфиге и попадать в отчёт.

#### Scenario: Нестабильное количество

- **WHEN** из 10 прогонов два дали 4 кейса вместо 5
- **THEN** `count_match_rate=0.8`
- **AND** вердикт `fail`
- **AND** в отчёте есть ссылки на проблемные прогоны

#### Scenario: Стабильная структура

- **WHEN** все 10 прогонов дали 5 кейсов, покрытие `BR` 100%, Jaccard ≥ 0.4
- **THEN** вердикт `pass`

### Requirement: Проверка деградации

- Система SHALL поддерживать baseline и candidate.
- Baseline SHALL задаваться либо `--baseline-model`, либо
  `--baseline-report`; ровно один вариант.
- Candidate SHALL задаваться `--candidate-model`.
- Обе ячейки SHALL использовать один `prompt_sha256`, `artifact_type`,
  `count`; `runs` MAY различаться, расхождение SHALL быть warning.
- Для вердикта обе ячейки SHALL иметь `runs >= 5`.
- Система SHALL считать `CellSummary` и сравнение.
- Система SHALL считать Wilson CI для долей (95%, z=1.96) и bootstrap CI для
  средних (1000 ресэмплов, seed=12345).
- Система SHALL считать абсолютную/относительную разницу и Cohen's h.
- Вердикт SHALL быть `insufficient_data` при `runs < 5`.
- Вердикт SHALL быть `regressed`, если pass@1 упал ≥ 0.20 и CI не
  пересекаются, или `invented_br_total` вырос ≥ 2, или
  `br_coverage_mean` упал ≥ 0.10, или `valid_json_rate` упал ≥ 0.20, или
  `mean_attempts` вырос ≥ 1.0.
- Вердикт SHALL быть `improved` симметрично; иначе `unchanged`.
- `--fail-on-regression` SHALL возвращать код 5 при `regressed`.
- `verdict.reasons` SHALL содержать конкретные числа и пороги.
- Baseline-файл SHALL создаваться живым прогоном, не руками.

#### Scenario: Несовпадение входа

- **WHEN** `prompt_sha256` baseline и candidate различаются
- **THEN** сравнение не запускается
- **AND** CLI возвращает код 4

#### Scenario: Мало прогонов

- **WHEN** `runs=3`
- **THEN** вердикт `insufficient_data`
- **AND** отчёт всё равно записывается

### Requirement: Отчёты

- Система SHALL писать `dz8/output/<run_id>/` с `manifest.json`,
  `normalized.md`, `prompt.txt`, `runs/run-NN/{request.json,response.txt,metrics.json}`,
  `report.json`, `report.md`.
- `run_id` SHALL быть `YYYYMMDDTHHMMSSZ-<6 hex>` UTC.
- `report.json` SHALL валидироваться pydantic с `extra="forbid"` и
  `schema_version`.
- `report.json` SHALL содержать `environment`, `input`, `prompt`
  (`artifact_type`, `count`, `presets`, `prompt_sha256`), `params`, `runs`,
  `summary`, `verdict`, `warnings`, чтобы отчёт годился как baseline.
- `git_commit` SHALL быть необязательным и `null`, если git недоступен.
- `report.md` SHALL генерироваться из `report.json`.
- Запись SHALL быть атомарной: временный файл + `os.replace`.
- Сырые ответы SHALL NOT попадать в Markdown целиком; только цитаты до 300
  символов.
- `report.json` SHALL быть источником правды.
- При несовпадении `schema_version` загрузка SHALL возвращать код 2.

#### Scenario: Запись отчёта

- **WHEN** серия завершена
- **THEN** в каталоге есть `report.json` и `report.md`
- **AND** `report.json` проходит pydantic-валидацию
- **AND** `report.md` содержит таблицу прогонов и вердикт

### Requirement: CLI

- CLI SHALL называться `llm-lab` и быть доступным как
  `python -m llm_degradation`.
- Подкоманды SHALL быть: `health`, `normalize`, `api-check`,
  `hallucinations`, `consistency`, `canary`, `degradation`, `report`.
- `main(argv) -> int` SHALL возвращать код, не вызывать `sys.exit` внутри
  подкоманд.
- `--json` SHALL печатать ровно один JSON-объект в stdout; логи — в stderr.
- Exit codes SHALL быть 0/2/3/4/5 по ТЗ.
- CLI SHALL NOT требовать интерактивного ввода.
- CLI SHALL NOT делать `ollama pull`.
- При ошибке `--json` SHALL печатать `ok=false` и `error={code,message,hint}`.

#### Scenario: Успешный health

- **WHEN** `llm-lab health --model llama3 --json`
- **THEN** stdout содержит один JSON с `ok=true`, `digest`, `models`
- **AND** код возврата 0

#### Scenario: Ollama недоступна

- **WHEN** Ollama недоступна
- **THEN** код возврата 3
- **AND** JSON содержит `hint` с командой проверки стенда

### Requirement: Конфигурация

- Конфиг SHALL быть TOML с секциями `[ollama]`, `[paths]`,
  `[thresholds]`.
- Приоритет SHALL быть: CLI > env > `config.toml` > defaults.
- Env SHALL включать `OLLAMA_BASE_URL`, `OLLAMA_MODEL`,
  `LLM_LAB_ROOT`, `LLM_LAB_INPUT_DIR`, `LLM_LAB_OUTPUT_DIR`,
  `DZ2_PROMPT_DIR`, `DZ2_PRESETS_DIR`,
  `AI_TESTGEN_NORMALIZER_CONFIG`.
- Неизвестные ключи SHALL давать предупреждение, не падение.
- Некорректные значения SHALL давать код 2 с ключом и ожидаемым типом.
- `timeout > 0`, `runs >= 1`, `count >= 1`, `num_ctx >= 512`,
  `num_predict >= 16`, `max_retries >= 0`, `temperature` в `[0, 2]`.
- Пороги SHALL быть в `[0, 1]`, кроме `degradation_attempts_increase_max`.

#### Scenario: Ошибка конфига

- **WHEN** `timeout = 0`
- **THEN** CLI возвращает код 2
- **AND** сообщение называет ключ `timeout`

### Requirement: Тесты и качество

- Unit-тесты SHALL проходить без Ollama и без сети.
- Unit-тесты SHALL использовать `httpx.MockTransport` и записанные ответы.
- Integration-тесты SHALL быть помечены `integration` и skip без
  Ollama/модели.
- UI-тесты SHALL использовать `streamlit.testing.v1.AppTest`.
- Минимум SHALL быть 60 unit-тестов.
- `ruff check src tests` и `mypy src` SHALL быть чистыми.
- Тесты SHALL NOT мокать нормализатор и валидаторы Dz2.
- Тесты SHALL NOT вызывать `ollama pull`.
- Тесты SHALL NOT содержать `assert True`.

#### Scenario: Unit без стенда

- **WHEN** Ollama не запущена
- **THEN** `pytest -m "not integration"` проходит
- **AND** integration-тесты не запускаются

#### Scenario: Integration на стенде

- **WHEN** Ollama и `llama3` доступны
- **THEN** `pytest -m integration` проходит или даёт skip с причиной
- **AND** отчёты пишутся в `dz8/output/`
