# Контрольный прогон метрик (`example_metrics_run`)

Пример воспроизводимого прогона пайплайна на обоих датасетах и обеих
моделях. Числа ниже получены на hold-out тестовой выборке; они не являются
порогами и не проверяются в unit-тестах.

## Команда

```bash
PYTHONPATH=dz9/src python -m model_quality_lab \
  --all-datasets --all-models \
  --train --predict --metrics --visualize
```

## Окружение и конфигурация

| Параметр | Значение |
|---|---|
| Дата прогона | 2026-11-04 |
| Python | 3.13.11 |
| scikit-learn | 1.9.1 |
| numpy | 2.5.4 |
| matplotlib | 3.11.2 |
| `RANDOM_STATE` | `42` |
| `TEST_SIZE` | `0.2` |
| `MODEL_MAX_ITER` | `5000` |
| Модели | `logistic_regression`, `perceptron` |
| Датасеты | `breast_cancer` (569), `pima_indians` (768) |

Обе модели обучаются внутри `Pipeline(StandardScaler, model)`, поэтому
признаки на разных шкалах не искажают сравнение.

## Результаты

| Датасет | Модель | Accuracy | Precision | Recall |
|---|---|---|---|---|
| `breast_cancer` | `logistic_regression` | 0.982 | 0.986 | 0.986 |
| `breast_cancer` | `perceptron` | 0.956 | 0.986 | 0.944 |
| `pima_indians` | `logistic_regression` | 0.714 | 0.609 | 0.519 |
| `pima_indians` | `perceptron` | 0.727 | 0.615 | 0.593 |

## Артефакты

- `output/metrics_breast_cancer_logistic_regression.png`
- `output/metrics_breast_cancer_perceptron.png`
- `output/metrics_pima_indians_logistic_regression.png`
- `output/metrics_pima_indians_perceptron.png`
- `output/metrics_comparison.png`

## Как воспроизвести

1. Установить зависимости: `uv sync` (или `uv pip install -r requirements.txt`).
2. Выполнить команду из раздела «Команда» из корня репозитория.
3. Сравнить таблицу с выводом `Comparison summary` в консоли.

Небольшие расхождения возможны при смене версий scikit-learn/numpy.
