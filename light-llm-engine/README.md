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

**Вариант 1 — совместно с единым ollama (рекомендуется для домашек).**
Один `ollama` с моделью кэшируется в общем volume `ohw_ollama_models` и
переиспользуется всеми проектами монорепо — модель скачивается один раз.

```bash
# один раз — стартуем общий сервис
cd d:/Otus/ohw
docker compose -f infra/compose.yaml up -d

# в каталоге проекта — ТОЛЬКО приложение (ollama уже запущен выше)
cd d:/Otus/ohw/light-llm-engine
docker compose up --build --abort-on-container-exit
```

Приложение ждёт готовности модели (healthcheck); `./input` и `./output`
монтируются как volume, `--abort-on-container-exit` гасит compose, когда
работа завершена. Сменить модель — `OLLAMA_MODEL=` в `infra/.env` +
`docker compose -f infra/compose.yaml restart ollama`.

**Вариант 2 — автономно (самодостаточно для проверки/сдачи).**
Поднимает `ollama` вместе с приложением в отдельном volume
(`ohw_ollama_models`) — удобно, когда общий сервис не удалось запустить
(например, ghcr.io недоступен). Требует остановки `ohw-ollama`, иначе
конфликт порта `11434`.

```bash
docker compose -f infra/compose.yaml stop          # освободить порт
docker compose --profile standalone up --build --abort-on-container-exit
```

GPU (NVIDIA): добавьте `compose.gpu.yaml` поверх `compose.yaml` и включите
профиль standalone:

```bash
docker compose -f infra/compose.yaml stop
docker compose --profile standalone -f compose.yaml -f compose.gpu.yaml up --build
```

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