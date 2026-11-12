# Дизайн: Streamlit-рабочее место

## Контекст

Пайплайн уже разделён на стадии, которые общаются через файлы в `output/`.
CLI-оркестрация живёт в `src/workflow/main.py`; чтобы UI не копировал её,
стадии выносятся в `src/workflow/stages.py`.

## Граница

```text
CLI (src/workflow/main.py) ─┐
                            ├──▶ src/workflow/stages.py ──▶ generator/quality/
UI  (src/ui/app.py)        ─┘                              visualization/reporting/
                                                            autotests/
```

`stages.py` не знает про `argparse` и Streamlit. Он принимает доменные объекты
(`GenerationRequest`, `ApiTestRequest`) и возвращает `PipelineData`/отчёт.
`main.py` добавляет логирование и коды возврата, `app.py` — виджеты и
отображение.

## Структура

```text
src/
├── workflow/
│   ├── main.py      # CLI, фазы, коды возврата
│   └── stages.py    # общие файловые стадии
└── ui/
    ├── __init__.py
    ├── __main__.py  # python -m src.ui
    ├── launcher.py  # streamlit run + адрес/порт
    └── app.py       # рабочее место
```

## Экранирование

Боковая панель:

- адрес Ollama (пресеты locale/`ohw-ollama`/ручной), кнопка «Загрузить модели»;
- модель из `/api/tags` или ручной ввод;
- таймаут, температура, папка результатов.

Вкладки:

| Вкладка | Содержание |
|---|---|
| 1. Требования | загрузка `.md`/`.txt`, редактор, URL, тип, кейсы, повторы, пресеты |
| 2. Генерация | запуск, попытки, ошибки, Markdown и скачивание артефактов |
| 3. Метрики | средние, таблица по сценариям, boxplot/radar/bars |
| 4. Отчёт | `report.md`, `quality-metrics.json`, скачивание |
| 5. API-тесты | URL, контракт, число кейсов, запуск через шов |

## Поток данных UI

```text
uploaded .md/.txt
      │  (правки в text_area)
      ▼
output/ui-input/<name>
      │
      ▼
GenerationRequest → stages.generate → PipelineData + generation.json
      │
      ├── stages.assess → quality-metrics.json
      ├── stages.visualize → charts/*.png
      ├── stages.api_tests → api-tests.json
      └── stages.report → report.md + quality-metrics.json
```

`PipelineData` хранится в `st.session_state`; стадии можно перезапускать
отдельно, как в CLI. Если данных в сессии нет, вкладки показывают подсказку.

## Ошибки

- Нет модели — подсказка в боковой панели, генерация не стартует.
- Ollama недоступна — `st.error` с текстом ошибки, сессия сохраняется.
- Ошибка одного файла — показывается в списке, остальные сценарии остаются.
- `--api-tests` без адаптера — `st.error` с сообщением шва, без падения.
- Ошибка записи — `st.error`, остальные вкладки доступны.

## Тестирование

`streamlit.testing.v1.AppTest` запускает `app.py` в процессе. Сеть не нужна:

- загрузка файла и счётчики;
- переключение типа/кейсов/повторов;
- генерация с подменённым `OllamaClient.chat`;
- метрики, графики и отчёт по сгенерированным данным;
- вкладка API-тестов с заглушкой;
- отсутствие запросов к Ollama при старте.

## Развёртывание

Один образ на оба фронта: `streamlit` входит в зависимости, `ui`-сервис
запускает `python -m src.ui` и публикует `8501`. CLI-сервис остаётся с прежним
entrypoint. Оба используют `ohw_net` и `OLLAMA_BASE_URL=http://ohw-ollama:11434`.

## Отклонённые альтернативы

- **Копировать оркестрацию в UI.** Два источника правды для snapshot/report;
  поэтому стадии вынесены в `stages.py`.
- **Отдельный образ только для UI.** Дублирование сборки; Streamlit добавлен в
  основные зависимости.
- **FastAPI + SPA.** Больше кода и ещё один сервис; для ручного сценария
  Streamlit достаточно.
