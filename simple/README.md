# DZ3 Simple — API Test Generator (pytest output)

Генератор API-автотестов через локальную LLM (Ollama). Промпт берётся из файла (папка `input/`), отправляется на сервер Ollama, сгенерированный pytest-код сохраняется в `output/` и запускается.

## Требования

- Python 3.13+, uv
- Docker (для shared ollama из `ohw/infra/`)

## Быстрый старт

```bash
# 1. Убедитесь, что shared ollama запущен
cd ../infra && docker compose up -d
cd ../dz3/simple

# 2. Установить зависимости
uv sync --dev

# 3. Положить промпт в input/prompt.txt (см. примеры в examples/input/)
# 4. Запустить
uv run api-testgen
```

## Папка input/

По аналогии с DZ2:

- `input/` — рабочая папка (git-ignored). Сюда кладёшь файл промпта `prompt.txt`, который уйдёт на сервер Ollama.
- `examples/input/` — шаблоны и примеры (в репозитории):
  - `prompt-template.txt` — общий шаблон промпта (LLM сама решает, какие эндпоинты проверять).
  - `jsonplaceholder.txt` — конкретный запрос для https://jsonplaceholder.typicode.com/guide/ (с учётом того, что create/update/delete «фейковые»).

```bash
# взять пример как основу для своей задачи
cp examples/input/jsonplaceholder.txt input/prompt.txt
```

## CLI

```
api-testgen [опции]

  --output-dir DIR    Папка для сгенерированных файлов (по умолчанию: ./output)
  --input-dir DIR     Папка с промптами (по умолчанию: ./input)
  --prompt-file FILE  Конкретный файл промпта (по умолчанию: <input-dir>/prompt.txt)
  --max-retries N     Макс. попыток генерации (по умолчанию: 3)
  --no-run            Только сгенерировать, не запускать pytest
  --save-prompt       Сохранить промпт в файл (для отладки)
```

## Docker

```bash
docker compose up --build app
```

## Структура

```
simple/
├── src/api_testgen/    Исходный код
│   ├── cli.py          CLI + пайплайн
│   ├── config.py       Конфигурация из .env
│   ├── prompt.py       Загрузка промпта из файла
│   ├── ollama.py       Клиент Ollama
│   ├── extractor.py    Извлечение кода из ответа LLM
│   ├── runner.py       Запуск pytest
│   └── models.py       Модели данных
├── tests/              Unit-тесты
├── examples/input/     Шаблоны и примеры промптов (в репо)
├── input/              Рабочая папка с промптами (git-ignored)
├── output/             Сгенерированные файлы (git-ignored)
├── compose.yaml        Docker Compose
├── Dockerfile
└── .devcontainer/      Dev Container
```