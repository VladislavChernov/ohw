# Как пользоваться проектом

Практическое руководство: как запустить пайплайн на Windows и Linux/macOS,
как добавить свой датасет и где искать результаты.

## 1. Что нужно

**Вариант A — Docker (рекомендуется).** Ничего кроме Docker ставить не надо:
зависимости устанавливаются внутри контейнера при каждом запуске.

**Вариант B — локальный Python.** Python 3.13+ и [uv](https://docs.astral.sh/uv/)
(или `pip`). Зависимости описаны в `pyproject.toml` и `requirements.txt`.

## 2. Запуск

### Windows

```bat
:: полный демо-прогон: оба датасета x обе модели
run_windows.bat

:: одна модель на одном датасете
run_windows.bat --dataset breast_cancer --model logistic_regression ^
  --train --predict --metrics --visualize
```

Скрипт сам находит корень репозитория, монтирует его в контейнер и передаёт
все аргументы в `model_quality_lab`. Без аргументов выполняется полный прогон.

### Linux / macOS

```bash
chmod +x run_linux_mac.sh      # один раз
./run_linux_mac.sh

./run_linux_mac.sh --dataset pima_indians --model perceptron \
  --train --predict --metrics --visualize
```

Если `chmod` недоступен, используйте `bash run_linux_mac.sh`.

### Без Docker (локально)

```bash
cd dz9
uv sync
uv run python -m model_quality_lab \
  --all-datasets --all-models --train --predict --metrics --visualize
```

Через `pip` (без установки пакета):

```bash
cd dz9
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
PYTHONPATH=src python -m model_quality_lab --dataset heart --train --predict --metrics
```

### Флаги CLI

| Флаг | Значение |
|---|---|
| `--dataset NAME` | датасет: `heart`, `breast_cancer`, `pima_indians` или `csv:<path>` |
| `--all-datasets` | прогнать `breast_cancer` и `pima_indians` |
| `--model NAME` | `logistic_regression` или `perceptron` |
| `--all-models` | прогнать обе модели и построить сравнение |
| `--train` | обучить модель |
| `--predict` | получить предсказания на тесте |
| `--metrics` | посчитать accuracy / precision / recall |
| `--save-model` | сохранить модель в `output/model_<dataset>_<model>.pkl` |
| `--visualize` | сохранить графики метрик |
| `--test-size` | доля тестовой выборки (по умолчанию `0.2`) |
| `--random-state` | seed разбиения и обучения (по умолчанию `42`) |

## 3. Как добавить датасет

### Вариант A. CSV без изменения кода

Подходит, если данные уже лежат в одном CSV.

1. Подготовьте файл: колонки с признаками `feature_1 … feature_N` и
   обязательная колонка `label` со значениями `0` или `1`.

   ```csv
   feature_1,feature_2,feature_3,label
   5.1,3.5,1.4,0
   6.2,2.9,4.3,1
   ```

2. Положите файл в `dz9/data/`, например `dz9/data/my_dataset.csv`.

3. Запустите с локатором `csv:<path>`:

   ```bat
   run_windows.bat --dataset csv:data/my_dataset.csv --train --predict --metrics --visualize
   ```

   ```bash
   ./run_linux_mac.sh --dataset csv:data/my_dataset.csv --train --predict --metrics --visualize
   ```

   Путь указывается относительно `dz9/` (рабочая директория внутри
   контейнера — `/app/dz9`). Абсолютный путь тоже работает, если файл
   доступен внутри контейнера.

**Требования к CSV:** непустой файл; есть колонка `label`; все значения
числовые; метки только `0` и `1`; присутствуют оба класса. Для
стратифицированного split нужно минимум по 4 объекта каждого класса
(8 строк суммарно). При нарушении загрузка завершится понятной ошибкой.

### Вариант B. Добавить датасет в registry

Нужен, если хочется использовать короткий ключ вместо длинного `csv:<path>`
или если данные загружаются не из CSV (например, из другого источника).

В `src/model_quality_lab/dataset.py`:

```python
def _load_my_dataset() -> tuple[np.ndarray, np.ndarray]:
    """Load my dataset from dz9/data/my_dataset.csv."""
    return _read_csv(DATA_DIR / "my_dataset.csv")


DATASET_REGISTRY: Final[dict[str, DatasetLoader]] = {
    "heart": _load_heart,
    "breast_cancer": _load_breast_cancer,
    "pima_indians": _load_pima_indians,
    "my_dataset": _load_my_dataset,   # <- новая строка
}
```

После этого:

```bash
python -m model_quality_lab --dataset my_dataset --train --predict --metrics --visualize
```

Если метки в исходных данных не `0/1`, приведите их к бинарному виду в
загрузчике (например, `(y == "yes").astype(int)`).

### Встроенные датасеты

| Ключ | Источник | Объём |
|---|---|---|
| `heart` | синтетический демо-CSV в `data/heart.csv` | 10 строк, 5 признаков |
| `breast_cancer` | встроен в scikit-learn, работает офлайн | 569 строк, 30 признаков |
| `pima_indians` | `data/pima_indians.csv` (UCI Pima Indians Diabetes) | 768 строк, 8 признаков |

### Какие метрики попадают на графики

Список задаётся в `src/model_quality_lab/config.py` и общий для обоих типов
графиков:

```python
PLOT_METRICS: dict[str, bool] = {
    "accuracy": True,
    "precision": True,
    "recall": True,
}
```

`False` убирает метрику с графиков; при этом она всё равно считается и
печатается в консоли. Порядок ключей в словаре задаёт порядок столбцов.
Если выключить все три, построение графика завершится ошибкой
`no metrics enabled for plotting`.

## 4. Где смотреть результаты

- **Консоль** — метрики каждого прогона, матрица ошибок
  (`Confusion matrix`, TP/TN/FP/FN) и итоговая таблица `Comparison summary`.
- **`dz9/output/`** — графики (PNG):
  - `metrics_<dataset>_<model>.png` — accuracy / precision / recall одной
    модели;
  - `metrics_comparison.png` — сравнение всех прогонов (появляется, когда
    запущено больше одного датасета/модели).
- **`dz9/output/model_<dataset>_<model>.pkl`** — сохранённая модель (только с
  флагом `--save-model`).
- **Документация:**
  - [`example_metrics_run.md`](example_metrics_run.md) — контрольный прогон и
    разбор датасетов;
  - [`risks.md`](risks.md) — риски и ограничения методологии;
  - [`METRICS_GLOSSARY.md`](METRICS_GLOSSARY.md) — что означают метрики.

`output/` не хранится в git целиком: графики и модели генерируются заново при
каждом запуске. Исключение — пять графиков последнего прогона: они
закоммичены, чтобы ссылки в документации работали на GitHub.

## 5. Тесты и качество кода

| Платформа | Команда |
|---|---|
| Windows (cmd) | `run_tests_docker.bat` |
| Windows (PowerShell) | `powershell -File run_tests_docker.ps1` |
| Linux / macOS | `bash run_tests_docker.sh` |
| Локально | `uv run pytest -q` / `uv run ruff check src tests` / `uv run mypy src` |

Скрипты прогоняют `ruff`, `mypy` и `pytest` в Docker и пишут лог в
`dz9/test_output.log`.

## 6. Частые проблемы

- **`docker: command not found`** — установите Docker Desktop / Docker Engine.
- **`Permission denied` для `.sh`** — `chmod +x run_linux_mac.sh` или
  запускайте через `bash`.
- **`Dataset not found` для `csv:`** — путь считается от `dz9/`; проверьте,
  что файл лежит внутри смонтированного репозитория.
- **`Dataset must contain both classes`** — в данных только один класс;
  стратифицированное разбиение невозможно.
- **`labels must be in {0, 1}`** — приведите метки к бинарному виду.
- **Графики не появились** — добавьте `--visualize`; файлы смотрите в
  `dz9/output/`.
