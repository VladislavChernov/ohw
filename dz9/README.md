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
- **Метрики**: accuracy, precision, recall; матрица ошибок (TP/TN/FP/FN)
  печатается в консоль; fail-safe `0.0` при нулевом знаменателе.
- **Сохранение модели**: pickle (`output/*.pkl`).
- **Визуализация**: одиночный график метрик и сравнительный график по
  нескольким моделям/датасетам; набор метрик настраивается в `PLOT_METRICS`.
- **Детерминизм**: фиксированный seed, стратифицированный split.

## Как получить проект

```bash
git clone https://github.com/VladislavChernov/ohw.git
cd ohw/dz9
```

Все команды ниже выполняются из папки `dz9`: скрипты сами находят корень
репозитория и монтируют его в контейнер. Если проект уже склонирован,
обновите его:

```bash
cd ohw
git pull
cd dz9
```

## Быстрый старт

Нужен Docker (Docker Desktop на Windows/macOS, Docker Engine на Linux;
Docker Desktop должен быть запущен) — зависимости ставятся внутри контейнера.
Git нужен только чтобы скачать проект.

**Windows:**

```bat
run_windows.bat
```

**Linux / macOS:**

```bash
chmod +x run_linux_mac.sh
./run_linux_mac.sh
```

Без аргументов выполняется полный демо-прогон: оба датасета × обе модели,
метрики, графики и сравнительная сводка. Свои аргументы передаются как есть:

```bat
run_windows.bat --dataset breast_cancer --model logistic_regression --train --predict --metrics --visualize
```

Локальный запуск без Docker:

```bash
cd dz9
uv sync
uv run python -m model_quality_lab --all-datasets --all-models --train --predict --metrics --visualize
```

Проверить, что всё работает, можно quality gate:

```bash
# Windows (cmd)
run_tests_docker.bat

# Linux / macOS
bash run_tests_docker.sh
```

Ожидаемо: `ruff` и `mypy` без ошибок, все тесты `passed`.

Подробно — в [`HOWTO.md`](HOWTO.md).

## Структура

```
dz9/
├── README.md                # обзор проекта
├── HOWTO.md                 # как добавить датасет, запустить, где результаты
├── METRICS_GLOSSARY.md      # глоссарий метрик
├── example_metrics_run.md   # контрольный прогон и разбор датасетов
├── risks.md                 # риски и ограничения методологии
├── pyproject.toml           # пакет, зависимости, pytest/ruff/mypy
├── run_windows.bat          # запуск пайплайна (Windows)
├── run_linux_mac.sh         # запуск пайплайна (Linux/macOS)
├── run_tests_docker.{bat,ps1,sh}  # quality gate (ruff/mypy/pytest)
├── data/
│   ├── heart.csv            # синтетический демо-набор (10 строк)
│   └── pima_indians.csv     # Pima Indians Diabetes (768 строк)
├── output/                  # 5 PNG последнего прогона (в git); модели и прочее gitignored
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
| `PLOT_METRICS` | `accuracy`, `precision`, `recall` — флаги `True`/`False`: что рисовать на графиках |

## Тесты и качество

Скрипты quality gate (ruff + mypy + pytest в Docker):

| Платформа | Команда |
|---|---|
| Windows (cmd) | `run_tests_docker.bat` |
| Windows (PowerShell) | `powershell -File run_tests_docker.ps1` |
| Linux / macOS | `bash run_tests_docker.sh` |

Локально:

```bash
cd dz9
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Требования: unit-тесты без сети и внешних данных; `ruff` и `mypy` — чисто.

## Документация

- [`HOWTO.md`](HOWTO.md) — как добавить датасет, запустить пайплайн и где
  смотреть результаты.
- [`METRICS_GLOSSARY.md`](METRICS_GLOSSARY.md) — что означают accuracy,
  precision, recall, матрица ошибок и стратификация.
- [`example_metrics_run.md`](example_metrics_run.md) — контрольный прогон,
  разбор датасетов и интерпретация метрик.
- [`risks.md`](risks.md) — риски и ограничения методологии: hold-out split,
  пропуски в Pima, отсутствие подбора гиперпараметров.

## Источник данных

`pima_indians.csv` — Pima Indians Diabetes (UCI), приведён к формату
`feature_1…feature_8,label`.

## Лицензия

Проект распространяется под лицензией MIT. Полный текст — в общем файле
репозитория: [`../LICENSE.md`](../LICENSE.md).

```text
MIT License

Copyright (c) 2026 VladislavChernov
```
