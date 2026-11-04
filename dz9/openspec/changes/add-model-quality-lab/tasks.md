# Tasks: лаборатория качества обученной модели (Dz9)

Статус: `[ ]` — ожидает; `[x]` — выполнено и проверено.

## 0. Подготовка и правила

- [x] 0.1 Прочитать `dz9/TZ.md`, `dz9/openspec/project.md`,
      `openspec/changes/add-model-quality-lab/{proposal,design}.md`,
      `specs/model-quality-lab/spec.md`.
- [x] 0.2 Прочитать `dz9/hi level task.txt`.
- [x] 0.3 Зафиксировать открытые вопросы ТЗ (15) до начала кода.

## 1. Каркас пакета

- [x] 1.1 `dz9/pyproject.toml`:
      - `name = "model-quality-lab"`;
      - `requires-python = ">=3.13"`;
      - deps: `scikit-learn`, `matplotlib`;
      - dev group: `pytest`, `ruff`, `mypy`;
      - `build-system`: `uv_build`, `module-name = "model_quality_lab"`;
      - `[tool.pytest.ini_options] markers`: unit (без интеграции);
      - `[tool.ruff]`, `[tool.mypy]` без ослаблений.
- [x] 1.2 `dz9/src/model_quality_lab/__init__.py` с `__version__`.
- [x] 1.3 `dz9/src/model_quality_lab/__main__.py` — точка входа (CLI).
- [x] 1.4 `QualityLabConfig` (seed, test_size, model_name, data_dir,
      output_dir) в `src/model_quality_lab/config.py`.

## Gate перед фазой 2

Фазу 2 начинаем только при зелёном гейте:

- [x] Gate 2.0: ТЗ `dz9/TZ.md` (v1.0) прочитано и принято как baseline;
- [x] Gate 2.0: change `add-model-quality-lab` (proposal/design/tasks/spec)
      прочитаны и согласованы с ТЗ;
- [x] Gate 2.0: открытые вопросы ТЗ §15 закрыты (dataset — механизм + примеры;
      model — 2 модели; формат CSV — фиксирован);
- [x] Gate 2.0: критерий входа в фазу 2 известен (tasks 2.1–2.7).

**Gate = green**, когда все пункты Gate 2.0 отмечены `[x]`.

## 2. Датасет и разбиение (FR-1)

- [x] 2.1 `dataset.py`: generic registry + `load_dataset(name) -> DatasetSplit`
      (`heart`, `breast_cancer`, `pima_indians`, локатор `csv:<path>`).
- [x] 2.2 Локатор `csv:<path>`: CSV с колонками `feature_1…feature_N` +
      бинарная `label ∈ {0, 1}`.
- [x] 2.3 Стратификация: `train_test_split(test_size, random_state=seed,
      stratify=y)`.
- [x] 2.4 Валидация: непустой файл/набор, наличие `label`, метка ∈ {0, 1},
      два класса; split без пересечений.
- [x] 2.5 Запуск по ключу: `--dataset NAME` и `--all-datasets`
      (`breast_cancer` → `pima_indians`).
- [x] 2.6 Тесты `test_dataset.py`.
- [x] 2.7 Проверка: unit-тесты зелёные; `ruff`, `mypy` — чисто.

## 3. Модель и обучение (FR-2)

- [x] 3.1 `model.py`: `build_model(name)` (`logistic_regression`,
      `perceptron`; MLP исключена).
- [x] 3.2 `train.py`: `train(model_name, dataset)`.
- [x] 3.3 Детерминизм: фиксированный seed.

## 4. Предсказания и метрики (FR-3, FR-4)

- [x] 4.1 `predict.py`: `predict(model, dataset) -> y_pred`.
- [x] 4.2 `metrics.py`: confusion matrix + accuracy/precision/recall,
      fail-safe на нулевой знаменатель.

## 5. Сохранение и визуализация (FR-6)

- [x] 5.1 `persistence.py`: `save_model(model, path)`, `load_model(path)`.
- [x] 5.2 `visualize.py`: matplotlib, сохранение PNG в `output/`;
      `plot_metrics` (одна модель) и `plot_comparison` (сравнение моделей).

## 6. Тесты

- [x] 6.1 `tests/test_basic.py`: точные значения метрик на микроданных;
      нулевые знаменатели; confusion matrix.
- [x] 6.2 `tests/test_dataset.py`: registry, split, стратификация,
      `csv:<path>`, ошибки валидации.
- [x] 6.3 `tests/test_basic.py`: обучение на синтетике, roundtrip,
      сохранение графиков (одиночный и сравнительный).
- [x] 6.4 `pytest`, `ruff`, `mypy` — чисто.

## 7. Финальная проверка и документация

- [x] 7.1 Обновить `dz9/TZ.md`, OpenSpec, промпт `dz9/prompts/code-writer.md`.
- [x] 7.2 Финальный отчёт по DoD.
