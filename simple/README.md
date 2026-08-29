# DZ3 Simple — API Test Generator (pytest output)

Генератор API-автотестов через локальную LLM (Ollama). Скачивает OpenAPI-спецификацию, отправляет промпт модели, получает pytest-код и запускает его.

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

# 3. Запустить
uv run api-testgen
```

## CLI

```
api-testgen [опции]

  --output-dir DIR    Папка для сгенерированных файлов (по умолчанию: ./output)
  --target-url URL    URL целевого API (по умолчанию: https://jsonplaceholder.typicode.com)
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
│   ├── swagger.py      Парсер OpenAPI
│   ├── prompt.py       Билдер промпта
│   ├── ollama.py       Клиент Ollama
│   ├── extractor.py    Извлечение кода из ответа LLM
│   ├── runner.py       Запуск pytest
│   └── models.py       Модели данных
├── tests/              Unit-тесты
├── output/             Сгенерированные файлы (git-ignored)
├── compose.yaml        Docker Compose
├── Dockerfile
└── .devcontainer/      Dev Container
```
