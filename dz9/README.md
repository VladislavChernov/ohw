# model-quality-lab
## Документация к ДЗ №9 (Otus)

### Описание проекта
**model-quality-lab** — это учебный Python-пакет для реализации полного конвейера машинного обучения end-to-end: загрузка данных, обучение модели, предсказания, вычисление метрик качества, сохранение модели и визуализация результатов.

Проект реализуется как обычный Python-пакет без своего контейнера, сервиса и портов: переиспользуется общий стенд `infra/` и окружение по аналогии с Dz8.

### Цель
Создать систему проверки качества обученной модели с seguinte функциями:
1. Загрузка датасета и разделение на train/test
2. Реализация модели на Python 3 и обучение
3. Предсказания на тестовом наборе
4. Вычисление метрик качества (accuracy, precision, recall)
5. Сохранение обученной модели
6. Автотесты
7. Визуализация метрик

### Структура проекта
```
dz9/
├── data/                # Тестовые/демо-наборы данных
│   └── heart.csv        # Синтетический датасет (11 строк, 5 фич + binary label)
├── output/              # Артефакты (модель, графики)
├── src/model_quality_lab/  # Основной пакет кода
│   ├── __init__.py      # Package init
│   ├── __main__.py      # CLI entry point
│   ├── config.py        # Конфигурация (RANDOM_STATE=42, TEST_SIZE=0.25)
│   ├── dataset.py       # CSV loading via csv module, stratified split
│   ├── model.py         # LogisticRegression/Perceptron builder
│   ├── train.py         # Training function
│   ├── predict.py       # Prediction function
│   ├── metrics.py       # Accuracy/precision/recall with fail-safe 0.0
│   ├── persistence.py   # Model save/load via pickle
│   └── visualize.py     # Matplotlib plots (Agg headless backend)
├── tests/               # Unit tests (9 tests, all passing)
│   └── test_basic.py
├── requirements.txt     # Зависимости: scikit-learn, matplotlib, numpy
└──                      # run_tests_docker.bat, run_all_models.bat
```

### Стек и конвенции
- **Python**: 3.13, `uv`, lock-файл коммитится
- **ML**: `scikit-learn`, `matplotlib` (+ numpy через зависимости sklearn)
- **Тесты**: `pytest`, `ruff`, `mypy`
- **Язык**: код и комментарии — на английском; документация — на русском
- **Критические запреты**:
  - **Запрет на Pandas**: категорически ЗАПРЕЩЕНО использовать библиотеку `pandas`. Чтение и парсинг CSV — строго встроенный модуль `csv` или `numpy.genfromtxt`
  - **Fail-safe metrics**: precision=0.0, recall=0.0 когда TP+FP=0 или TP+FN=0 (деление на ноль → 0.0)
  - **Headless plotting**: matplotlib с backend `Agg`, сохранение в файл без открытия окон

### Функционал

#### Загрузка данных
```python
from model_quality_lab.dataset import load_dataset

ds = load_dataset("dz9/data/heart.csv")
# Возвращает DatasetSplit с X_train, X_test, y_train, y_test
# Stratified split с фиксированным seed для репродуцируемости
```

#### Обучение модели
```python
from model_quality_lab import build_model, train

model = build_model("logistic_regression")
trained_model = train("logistic_regression", ds)
```

#### Предсказания
```python
from model_quality_lab import predict

y_pred = predict(trained_model, ds)
```

#### Метрики
```python
from model_quality_lab import compute_metrics

metrics = compute_metrics(y_true, y_pred)
# Возвращает: {"accuracy": float, "precision": float, "recall": float}
# Fail-safe: precision=0.0 если TP+FP=0, recall=0.0 если TP+FN=0
```

#### Сохранение/загрузка модели
```python
from model_quality_lab import save_model, load_model

save_model(trained_model, "dz9/output/model.pkl")
loaded = load_model("dz9/output/model.pkl")
```

#### Визуализация
```python
from model_quality_lab import plot_metrics

plot_metrics(metrics, output_path="dz9/output/metrics_plot.png")
# Сохраняет PNG с headless backend
```

#### Сравнение моделей
```python
from model_quality_lab.visualize import plot_comparison

path = plot_comparison(
    {
        "logistic_regression": {"accuracy": 0.9, "precision": 0.8, "recall": 0.85},
        "perceptron": {"accuracy": 0.7, "precision": 0.6, "recall": 0.75},
    },
    output_path="dz9/output/metrics_comparison.png",
)
# Сохраняет PNG со сравнением метрик нескольких моделей
```

#### CLI запуск
```bash
# Один модель
python -m model_quality_lab.__main__ --model logistic_regression --train --predict --metrics --visualize

# Все модели sequentially + сравнительный график
python -m model_quality_lab.__main__ --all-models --train --predict --metrics --visualize
```

### Unit тесты
Всего 9 тестов, все проходят:
1. `test_load_dataset` — загрузка и split
2. `test_build_model` — построение модели
3. `test_train_model` — обучение
4. `test_predict` — предсказания
5. `test_compute_metrics` — вычисление метрик (с проверкой значений)
6. `test_compute_metrics_zero_denominator` — fail-safe при делении на ноль
7. `test_save_load_model` — сохранение/загрузка roundtrip
8. `test_compute_confusion_matrix` — матрица ошибок
9. `test_plot_metrics` — генерация графика

Запуск: `pytest tests/test_basic.py -v` — 9 passed in ~5s

### Конфигурация
Основные настройки в `src/model_quality_lab/config.py`:
- `RANDOM_STATE = 42` — Seed для репродуцируемости
- `TEST_SIZE = 0.25` — Доля тестовой выборки
- `DEFAULT_MODEL_NAME = "logistic_regression"` — Модель по умолчанию
- `SUPPORTED_MODELS = ["logistic_regression", "perceptron"]` — Поддерживаемые модели

### Ограничения (Boundaries)
**Входит в работу:**
- `dz9/src/model_quality_lab/` — модули: dataset, model, train, predict, metrics, persistence, visualize
- `dz9/tests/` — unit-тесты
- `dz9/data/` — тестовый/демо-набор
- `dz9/output/` — артефакты (модель, график)
- документация: TZ, спеки, README

**Не входит в работу:**
- обучение/файнтюнинг модели вне пакета
- LLM-агент, API, UI, docker-компоненты
- новые зависимости без обоснования
- ручной ввод порогов после получения первых результатов

### Открытые вопросы (Open Questions)
1. **Формат и источник датасета** — CSV с колонками `feature_*` + `label`; путь по умолчанию `dz9/data/`. Нужен ли внешний датасет или только синтетический?
2. **Тип модели** (скользящий перцептрон / логистическая регрессия / малая нейросеть) — фиксируется в `config.py`.
3. **Единицы и обработка деления на ноль** при `TP+FP = 0` / `TP+FN = 0`.
4. **Инструмент визуализации** (matplotlib — рекомендован).
5. **CLI для запуска конвейера** — пока не требуется.

### Definition of Done
- [ ] пакет реализован по разделам 5–10
- [ ] unit-тесты зеленые, ruff/mypy чисто
- [ ] ТЗ и спеки актуальны после изменений

### Лицензия
Учебный проект (Otus, ДЗ9).