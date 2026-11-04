# Design: лаборатория качества обученной модели (Dz9)

## Кто чем владеет

```
dz9/data/<dataset>.csv
    │  dataset.load(name)
    ▼
dz9/src/<package>/dataset.py      # DatasetSplit
    │  model.build + train.fit
    ▼
dz9/src/<package>/train.py        # TrainedModel
    │  predict.predict → y_pred (test)
    ▼
dz9/src/<package>/predict.py      # y_pred
    │  metrics.confusion_matrix → accuracy/precision/recall
    ├──▶ persistence.save         # output/model/
    └──▶ visualize.plot           # output/*.png
            │
            ▼
dz9/tests/                        # unit: dataset, model, train, predict,
                                  #       metrics, persistence, visualize
```

## Поток данных

1. `dataset.load(name) -> DatasetSplit(train: X, y_train; test: X, y_test)` —
   `name` из registry или локатор `csv:<path>` на сторонний датасет.
2. `model.build(name) -> estimator`.
3. `train.fit(estimator, X_train, y_train) -> estimator`.
4. `predict.predict(estimator, X_test) -> y_pred`.
5. `metrics.build(y_true, y_pred) -> Metrics` (confusion matrix + 3 метрики).
6. `persistence.save(estimator)`, `visualize.plot(metrics, path)`.

## Решения

### Датасеты

- Generic mechanism: registry датасетов + локатор `csv:<path>`.
- Реестр: `breast_cancer` (встроенный в scikit-learn, без скачивания),
  `pima_indians` (CSV в `dz9/data/`, офлайн) и `heart` (синтетический
  демо-набор).
- Формат CSV стороннего датасета: колонки признаков `feature_1…feature_N`
  + бинарная метка `label ∈ {0, 1}`.
- Для пользовательского датасета — достаточно положить файл в `dz9/data/`
  и передать локатор `csv:<path>`.
- Датасет выбирается ключом (`--dataset`); конвейер выполняется для каждого
  выбранного ключа (`--all-datasets` — последовательный прогон).

### Разбиение

- `train_test_split(test_size=0.2, random_state=42, stratify=y)`.
- Один split на все данные, без повторного использования тестовой выборки.
- Проверка двух классов в train.

### Модель

- Делегирование в scikit-learn (`LogisticRegression` / `Perceptron`).
- Обе модели обёрнуты в `Pipeline(StandardScaler, model)`: признаки на разных
  шкалах, без масштабирования перцептрон деградирует.
- Фиксированный `random_state`, `max_iter` ограничен конфигом.
- Малая нейросеть (`MLPClassifier`) в объём работ не входит.

### Метрики

- Матрица ошибок строится из фактических `y_true`/`y_pred`.
- `accuracy = (TP + TN) / N`.
- `precision = TP / (TP + FP)`.
- `recall = TP / (TP + FN)`.
- При знаменателе 0 метрика fail-safe равна `0.0` (нет деления на ноль).

### Сохранение

- pickle (`persistence.save`) в `dz9/output/`, загрузка обратно.

### Визуализация

- matplotlib, headless backend (Agg), сохранение PNG.
- Одиночный график метрик и сравнительный график по нескольким
  моделям/датасетам (`plot_comparison`).

## Тесты

- Микроданные с известными ожиданиями (accuracy/precision/recall).
- Roundtrip сохранения/загрузки.
- Все unit-тесты без сети.

## Решённые вопросы (зафиксированы)

- Датасеты: `breast_cancer` (sklearn), `pima_indians` (CSV в репозитории),
  `heart` (демо); сторонние — через `csv:<path>`.
- Модели: `LogisticRegression` и `Perceptron` (MLP исключена).
- Формат внешнего CSV (`feature_*` + `label`) — зафиксирован в design;
  провалидирован на `heart.csv` и `pima_indians.csv`.
