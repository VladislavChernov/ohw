# light-llm-engine

Простой движок на Python: читает файлы запросов из папки `input`, отправляет их
содержимое локальной LLM (ollama в Docker) и записывает ответы модели в папку
`output` в виде markdown-документов.

## Как это работает

```
input/                output/
└── my_request.md  ──►   ├── ollama (/api/chat) ──► ответ модели
                        └── my_request.md   # ответ, обёрнутый в markdown
```

- Каждый файл `.md`/`.txt` во входной папке обрабатывается независимо.
- Ответ модели для файла `name.ext` записывается как `<output>/name.md`
  (расширение заменяется на `.md`); существующий документ перезаписывается.
- Коллизия имён (`auth.md` + `auth.txt`) — ошибка, обработка не начинается.

## Требования

- Python 3.13, менеджер пакетов **uv**.
- Локальная LLM через ollama (контейнер `ollama/ollama`) — модель из `OLLAMA_MODEL`.

## Запуск

### Локально

```bash
uv sync --dev
uv run python -m llm_engine                    # дефолтные ./input и ./output
uv run python -m llm_engine --input-dir custom --output-dir out --model qwen2.5:7b-instruct
```

### В Docker Compose

```bash
docker compose up --build --abort-on-container-exit
```

Движок и ollama поднимаются вместе: модель скачивается один раз (volume
`ollama_models`), приложение стартует только после готовности модели
(healthcheck), файлы монтируются из `./input` и `./output`. Флаг
`--abort-on-container-exit` гасит compose, когда приложение закончило работу.

Сменить модель — правьте `OLLAMA_MODEL` в `.env` (или переопределите при
запуске: `$env:OLLAMA_MODEL="llama3.2:3b"; docker compose up`).

GPU (NVIDIA): `docker compose -f compose.yaml -f compose.gpu.yaml up --build`.

## Конфигурация

Приоритет (от низшего к высшему):

```
defaults < light-llm-engine.toml < переменные окружения OLLAMA_* < аргументы CLI
```

Файл `light-llm-engine.toml` (в корне проекта):

```toml
[ollama]
base_url = "http://localhost:11434"   # внутри compose: http://ollama:11434
model = "qwen2.5:7b-instruct"         # обязательно (или OLLAMA_MODEL)

[paths]
input_dir = "input"   # в контейнере /data/input
output_dir = "output" # в контейнере /data/output

[generation]
temperature = 0.7
max_retries = 3
```

Переменные окружения: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`. Для Docker Compose —
`.env` в корне проекта.

## Коды выхода

| Код | Значение |
|-----|----------|
| 0   | успех |
| 2   | ошибки аргументов/входных файлов/конфига |
| 3   | сервис ollama недоступен |
| 4   | не удалось получить непустой ответ модели |

## Разработка

```
uv run pytest -q
uv run ruff check .
uv run mypy src tests
```