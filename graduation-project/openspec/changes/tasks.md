# Чек-лист реализации по фазам

Отметка `[x]` — реализовано и проверено; `[ ]` — осталось.

## Фаза 1. Структура и OpenSpec

- [x] Папка `graduation-project` с `src/`, `scripts/`, `openspec/`, `docs/`
- [x] `openspec/project.md` актуализирован под standalone-код и общий `infra/`
- [x] `openspec/changes/proposal.md` — фактический объём
- [x] `openspec/changes/design.md` — архитектура и поток данных
- [x] `openspec/changes/tasks.md` — этот чек-лист
- [x] `SDD.md` и `SDD_new.md` сведены в один `SDD.md`, `SDD_new.md` удалён
- [x] `pyproject.toml` без path-зависимостей; `[tool.uv] package = false`

## Фаза 2. Генерация (вендоринг DZ2)

- [x] `src/generator/`: `input_doc`, `normalizer`, `prompt`, `presets`,
  `handlers`, `service`, `validator`, `renderer`, `ollama_client`
- [x] `src/generator/templates/` (5 промптов + `_system/`)
- [x] `src/generator/presets/` (6 пресетов) и `normalizer.toml`
- [x] Импорты переписаны на `src.generator.*`, зависимость от `../dz2` убрана
- [x] `src/generator/pipeline.py`: один файл → `ScenarioResult`, `--runs N`
- [x] `src/config.py`: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`
- [x] Проверка установленной модели через `/api/tags` с предупреждением

## Фаза 3. Оценка качества

- [x] Readability: средняя длина предложения, оптимум 17 слов
- [x] Clarity of goals: 5 категорий по 0.2
- [x] Scenario coverage: `BR-N`, `None` без идентификаторов
- [x] Consistency: Jaccard повторных генераций, эвристика при `--runs 1`
- [x] Overall: взвешенное среднее с перенормировкой известных метрик
- [x] `None` → `n/a`, без подмены нулём
- [x] `src/models.py`: snapshot/report-сериализация

## Фаза 4. Визуализация и отчёт

- [x] Boxplot, radar, grouped bars (`Agg`)
- [x] `quality-metrics.json` с реальными `case_summaries`
- [x] `report.md` в формате задания, с детализацией и `n/a`
- [x] `generation.json` для отдельного запуска `--assess`
- [x] Файловое взаимодействие фаз `generate/assess/visualize/report`

## Фаза 5. CLI и развёртывание

- [x] `src/workflow/main.py`, `src/cli.py`, `scripts/cli.py`
- [x] Коды возврата 0/2/3/4/5/130, `--fail-under`
- [x] `Dockerfile` на `ohw-python:3.13`
- [x] `compose.yaml` только с `app`, внешняя сеть `ohw_net`
- [x] `infra.yaml` с компонентом `ollama`
- [x] `run.ps1`, `run.cmd`, `run.sh`
- [x] `input/registration-requirements.md`, `.env.example`, `.gitignore`

## Фаза 6. Тесты

- [x] Unit-тесты метрик, моделей, отчётов, генератора, workflow, графиков
- [x] `ruff check src scripts tests` без ошибок
- [x] `mypy src` без ошибок
- [x] Integration/e2e-тест с живой Ollama, источник — SDD проекта
- [x] Прогон e2e в Docker с `RUN_OLLAMA_INTEGRATION=1`: 1 passed, 53 deselected
- [x] Живой прогон на `qwen2.5:7b-instruct`, пример сохранён в `examples/live-run/`

## Фаза 8. Шов API-тестов и guard-тесты

- [x] `openspec/changes/add-api-tests-seam/` — предложение, дизайн, задачи
- [x] `openspec/specs/autotests/spec.md` — EARS-требования шва
- [x] `src/autotests/` — порт, модели, заглушка, сервис
- [x] CLI-флаги `--api-tests`, `--api-base-url`, `--api-contract`, `--api-cases`
- [x] `api-tests.json` и необязательный `api_tests` в JSON-отчёте
- [x] Раздел `## API-тесты` в `report.md` только при наличии данных
- [x] `openspec/changes/add-guard-tests/` — кодировка документации и лаунчеры
- [x] `tests/test_autotests.py`, `tests/test_docs_encoding.py`, `tests/test_launchers.py`
- [ ] Коммит и push реализации

## Фаза 7. Документация и отдача

- [x] `README.md` с быстрым стартом
- [x] `docs/user-guide.md`
- [x] `docs/metrics.md`
- [x] `docs/demo.md` с результатами живого прогона
- [x] Коммит и push в `master` (`f605a61`)
