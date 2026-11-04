# model-quality-lab (Dz9)

Учебный Python-пакет для сквозной проверки качества бинарной
классификации: загрузка датасета → train/test split → обучение модели →
предсказания → метрики → сохранение модели → графики.

## Возможности

- **Датасеты**: `heart` (демо), `breast_cancer` (встроен в scikit-learn),
  `pima_indians` (CSV в репозитории); сторонние — локатор `csv:<path>`.
- **Модели**: логистическая регрессия и перцептрон; выбор через CLI.
  Обе обёрнуты в `Pipeline(StandardScaler, model)`, чтобы признаки на разных
  шкалах не искажали сравнение.
- **Метрики**: accuracy, precision, recall; fail-safe `0.0` при нулевом
  знаменателе.
- **Сохранение модели**: pickle (`output/*.pkl`).
- **Визуализация**: одиночный график метрик и сравнительный график по
  нескольким моделям/датасетам.
- **Детерминизм**: фиксированный seed, стратифицированный split.

## Быстрый старт

Требуется Python 3.13+.

```bash
cd dz9
uv sync
uv run python -m model_quality_lab --train --predict --metrics --visualize
```

Запуск через Docker (зависимости ставятся внутри контейнера):

```bash
docker run --rm -v "D:/Otus/ohw:/app" -w /app \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  sh -c "uv pip install --system -r dz9/requirements.txt && \
         PYTHONPATH=/app/dz9/src python -m model_quality_lab \
         --all-datasets --all-models --train --predict --metrics --visualize"
```

## Структура

```
dz9/
├── pyproject.toml           # пакет, зависимости, pytest/ruff/mypy
├── data/
│   ├── heart.csv            # синтетический демо-набор (10 строк)
│   └── pima_indians.csv     # Pima Indians Diabetes (768 строк)
├── output/                  # генерируемые PNG-графики (gitignored)
├── src/model_quality_lab/
│   ├── config.py            # константы + QualityLabConfig
│   ├── dataset.py           # registry, csv:<path>, split, валидации
│   ├── model.py             # build_model(name)
│   ├── train.py             # train(model_name, dataset)
│   ├── predict.py           # predict(model, dataset)
│   ├── metrics.py           # confusion matrix + accuracy/precision/recall
│   ├── persistence.py       # save_model / load_model (pickle)
│   ├── visualize.py         # plot_metrics, plot_comparison
│   └── __main__.py          # CLI
└── tests/
    ├── test_basic.py        # model/train/predict/metrics/persistence/plots
    └── test_dataset.py      # registry, split, csv-локатор, ошибки
```

## CLI

```bash
# одна модель, один датасет
python -m model_quality_lab --model logistic_regression --dataset breast_cancer \
  --train --predict --metrics --visualize

# все модели
python -m model_quality_lab --all-models --train --predict --metrics --visualize

# оба примера датасетов + все модели + сравнительный график
python -m model_quality_lab --all-datasets --all-models \
  --train --predict --metrics --visualize

# сторонний CSV
python -m model_quality_lab --dataset csv:data/my_dataset.csv \
  --train --predict --metrics --visualize
```

Формат CSV: `feature_1,…,feature_N,label`, где `label ∈ {0,1}`.

## API

```python
from model_quality_lab.dataset import load_dataset
from model_quality_lab.train import train
from model_quality_lab.predict import predict
from model_quality_lab.metrics import compute_metrics
from model_quality_lab.persistence import save_model, load_model
from model_quality_lab.visualize import plot_metrics, plot_comparison

dataset = load_dataset("breast_cancer")
model = train("logistic_regression", dataset)
y_pred = predict(model, dataset)
metrics = compute_metrics(dataset.y_test, y_pred)

save_model(model, "output/model.pkl")
plot_metrics(metrics, output_path="output/metrics.png")
plot_comparison({"logistic_regression": metrics}, output_path="output/comparison.png")
```

## Конфигурация

`src/model_quality_lab/config.py`:

| Параметр | Значение |
|---|---|
| `RANDOM_STATE` | `42` |
| `TEST_SIZE` | `0.2` |
| `DEFAULT_MODEL_NAME` | `logistic_regression` |
| `SUPPORTED_MODELS` | `logistic_regression`, `perceptron` |
| `DEFAULT_DATASET_NAME` | `heart` |
| `MODEL_MAX_ITER` | `5000` |

## Тесты и качество

```bash
cd dz9
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Требования: unit-тесты без сети и внешних данных; `ruff` и `mypy` — чисто.

## Документация

- [`METRICS_GLOSSARY.md`](METRICS_GLOSSARY.md) — что означают accuracy,
  precision, recall, матрица ошибок и стратификация.
- [`example_metrics_run.md`](example_metrics_run.md) — контрольный прогон на
  двух датасетах и двух моделях с воспроизводимой командой.

## Источник данных

`pima_indians.csv` — Pima Indians Diabetes (UCI), приведён к формату
`feature_1…feature_8,label`.

## Лицензия

Учебный проект (Otus, ДЗ9).
