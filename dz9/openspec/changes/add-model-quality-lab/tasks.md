# Tasks: лаборатория качества обученной модели (Dz9)

Статус: `[ ]` — ожидает; `[x]` — выполнено и проверено.

## 0. Подготовка и правила

- [ ] 0.1 Прочитать `dz9/TZ.md`, `dz9/openspec/project.md`,
      `openspec/changes/add-model-quality-lab/{proposal,design}.md`,
      `specs/model-quality-lab/spec.md`.
- [ ] 0.2 Прочитать `dz9/hi level task.txt`.
- [ ] 0.3 Зафиксировать открытые вопросы ТЗ (15) до начала кода.

## 1. Каркас пакета

- [ ] 1.1 `dz9/pyproject.toml`:
      - `name = "model-quality-lab"`;
      - `requires-python = ">=3.13"`;
      - deps: `scikit-learn`, `matplotlib`;
      - dev group: `pytest`, `ruff`, `mypy`;
      - `build-system`: `uv_build`, `module-name = "model_quality"`;
      - `[tool.pytest.ini_options] markers`: unit (без интеграции);
      - `[tool.ruff]`, `[tool.mypy]` без ослаблений.
- [ ] 1.2 `dz9/src/model_quality/__init__.py` с `__version__`.
- [ ] 1.3 `dz9/src/model_quality/__main__.py` — точка входа (если CLI
      подтвердит ТЗ 15.5).
- [ ] 1.4 `dz9/config.py`: `QualityLabConfig` (seed, test_size, model_name,
      data_dir, output_dir).

## Gate перед фазой 2

Фазу 2 начинаем только при зелёном гейте:

- [ ] Gate 2.0: ТЗ `dz9/TZ.md` (v1.0) прочитано и принято как baseline;
- [ ] Gate 2.0: change `add-model-quality-lab` (proposal/design/tasks/spec)
      прочитаны и согласованы с ТЗ;
- [ ] Gate 2.0: открытые вопросы ТЗ §15 закрыты (dataset — механизм + примеры;
      model — предложение; формат CSV — фиксирован);
- [ ] Gate 2.0: критерий входа в фазу 2 известен (tasks 2.1–2.7).

**Gate = green**, когда все пункты Gate 2.0 отмечены `[x]`.

Только после green — переход к фазе 2. До green — фаза 2 не начинается.

## 2. Датасет и разбиение (FR-1)

- [ ] 2.1 `dataset.py`: generic registry + `load_dataset(name) -> DatasetSplit`
      (`breast_cancer`, `pima_indians`, локатор `csv:<path>`).
- [ ] 2.2 Локатор `csv:<path>`: CSV с колонками `feature_1…feature_N` +
      бинарная `label ∈ {0, 1}`.
- [ ] 2.3 Стратификация: `train_test_split(test_size, random_state=seed,
      stratify=y)`.
- [ ] 2.4 Валидация: непустой файл/набор, наличие `label`, метка ∈ {0, 1},
      два класса в train; split без пересечений.
- [ ] 2.5 Запуск по ключу: конвейер для выбранного датасета + отдельный прогон
      для второго (`breast_cancer` → `pima_indians`).
- [ ] 2.6 Тесты `test_dataset.py`.
- [ ] 2.7 Проверка: unit-тесты `test_dataset.py` зелёные;
      `pytest -m "not integration"`, `ruff check src tests`, `mypy src` — чисто.

## 3. Модель и обучение (FR-2)

- [ ] 3.1 `model.py`: `build_model(name)`.
- [ ] 3.2 `train.py`: `train(model, X_train, y_train)`.
- [ ] 3.3 Детерминизм: фиксированный seed.

## 4. Предсказания и метрики (FR-3, FR-4)

- [ ] 4.1 `predict.py`: `predict(model, X_test) -> y_pred`.
- [ ] 4.2 `metrics.py`: confusion matrix + accuracy/precision/recall,
      fail-safe на нулевой знаменатель.

## 5. Сохранение и визуализация (FR-6)

- [ ] 5.1 `persistence.py`: `save(model, path)`, `load(path)`.
- [ ] 5.2 `visualize.py`: matplotlib, сохранение PNG в `output/`.

## 6. Тесты

- [ ] 6.1 `tests/test_metrics.py`: точные значения на микроданных;
      нулевые знаменатели;
- [ ] 6.2 `tests/test_dataset.py`: split без пересечений, стратификация,
      ошибки;
- [ ] 6.3 `tests/test_train_predict_persistence_visualize.py`:
      обучение на синтетике, roundtrip, сохранение графика;
- [ ] 6.4 `pytest -m "not integration"`, `ruff`, `mypy`.

## 7. Финальная проверка и документация

- [ ] 7.1 Обновить `dz9/TZ.md`, OpenSpec, промпт `dz9/prompts/code-writer.md`.
- [ ] 7.2 Финальный отчёт по DoD.
