# Дельта спеки: testcase-generator

## ADDED Requirements

### Requirement: Внешние шаблоны промптов

User-prompt каждого типа артефакта SHALL собираться из отдельного файла
`<prompt_dir>/<type>.txt`.

- Система SHALL поддерживать шаблоны для `testcases`, `checklist`,
  `testplan`, `qareport`, `coveragematrix`.
- Система SHALL NOT содержать текст user-prompt в коде.
- Ведущие строки шаблона, начинающиеся с `#`, SHALL считаться
  комментариями и SHALL NOT попадать в промпт.

### Requirement: Плейсхолдеры шаблона

Система SHALL подставлять `{url}`, `{count}`, `{count_noun}`, `{positive}`,
`{negative}`, `{requirement_rule}`, `{content}`.

- Подстановка SHALL сохранять литеральные фигурные скобки, в частности
  пример `{"cases": [...]}`.
- **WHEN** URL отсутствует, строка `Target site: {url}` SHALL быть удалена.
- **WHEN** в требованиях нет `BR-N`, `{requirement_rule}` SHALL заменяться
  пустой строкой.
- **WHEN** после подстановки остаётся неизвестный плейсхолдер, система
  SHALL завершиться с кодом 2, не обращаясь к ollama.

### Requirement: Поиск каталога шаблонов

- Порядок поиска SHALL быть: `--prompt-dir`, `DZ2_PROMPT_DIR`,
  `./prompts`, каталог репозитория, пакетный каталог.
- Явно заданный каталог SHALL проверяться строго: его отсутствие или
  отсутствие файла нужного типа — ошибка с кодом 2.
- Молчаливый откат на встроенный промпт SHALL быть невозможен.

### Requirement: QA-отчёт

**WHEN** входной файл имеет тип `qareport`, система SHALL запросить
markdown-документ с разделами «Введение», «Тестовые сценарии», «Дефекты»,
«Рекомендации» и проверить их наличие и непустоту.

### Requirement: Матрица покрытия требований

**WHEN** входной файл имеет тип `coveragematrix`, система SHALL запросить
markdown-документ с разделом «Матрица покрытия требований» и
Markdown-таблицей.

- Валидатор SHALL требовать строку-разделитель таблицы.
- Строка данных таблицы SHALL NOT приниматься за разделитель.

### Requirement: Поставка шаблонов в Docker

- Образ SHALL содержать каталог шаблонов и переменную `DZ2_PROMPT_DIR`.
- Compose SHALL монтировать `./prompts` в каталог шаблонов без пересборки.

## MODIFIED Requirements

### Requirement: Объявление типа входного файла

К допустимым значениям `type` добавляются `qareport` и `coveragematrix`.

- Система SHALL принимать синонимы без учёта регистра, пробелов, `_` и `-`:
  `testcase`, `qa_report`, `qa-report`, `coverage_matrix`, `coverage`,
  `traceability`.

### Requirement: Формат ответа по типу артефакта

- JSON SHALL требоваться только для `testcases`.
- Для `checklist`, `testplan`, `qareport`, `coveragematrix` SHALL
  требоваться markdown и SHALL NOT отправляться `format="json"`.

### Requirement: Конфигурация

- К `[paths]` добавляется `prompt_dir`; CLI — `--prompt-dir`.
- Приоритет: CLI → конфиг → `DZ2_PROMPT_DIR` → `./prompts` → репозиторий.
