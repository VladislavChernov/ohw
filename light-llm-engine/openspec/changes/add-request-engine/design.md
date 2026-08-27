# Design: запросы к локальной LLM через движок

## Обзор

Однопоточный CLI-процесс. Поток данных:

```
input/*.{md,txt} ──► читаем файл ──► POST /api/chat (ollama) ──► ответ модели
      ──► рендер markdown ──► output/<stem>.md
```

## Структура пакета

```
src/llm_engine/
├── __init__.py
├── __main__.py           # python -m llm_engine
├── cli.py                # argparse, разрешение конфигурации, exit codes
├── config.py             # Config (dataclass), загрузка TOML + env
├── input_doc.py          # RequirementDoc, чтение входной папки, коллизии имён
├── ollama_client.py      # OllamaClient: POST /api/chat, классы ошибок
└── renderer.py           # рендер markdown-файла из ответа модели
```

## Ключевые решения

- **Конфигурация** — четыре слоя (нижний имеет наименьший приоритет):

  ```
  defaults < <имя>.toml < переменные окружения OLLAMA_* < аргументы CLI
  ```

  Файл `light-llm-engine.toml` ищется в текущем каталоге автоматически; отсутствие
  файла ошибкой не считается. Модель обязательна: `OLLAMA_MODEL` или
  `model` в секции `[ollama]`.

  ```toml
  [ollama]
  base_url = "http://localhost:11434"   # внутри compose: http://ollama:11434
  model = "qwen2.5:7b-instruct"         # обязательно
  timeout = 180.0
  # system = "опциональный системный промпт"

  [paths]
  input_dir = "input"                   # в контейнере /data/input
  output_dir = "output"                 # в контейнере /data/output

  [generation]
  temperature = 0.7
  max_retries = 3
  ```
- **OllamaClient** — оборачивает `httpx.Client`; метод
  `chat(system, user, temperature, *, json_mode=False)` шлёт
  `POST {base_url}/api/chat`, возвращает `message.content` как строку.
  Ошибки сети → `OllamaConnectionError` (exit 3), не-200 или битый ответ →
  `OllamaResponseError` (exit 3).
- **Файлы** — поддерживаются `.md` и `.txt`; каждый файл — независимый запрос.
  Коллизия имён (`auth.md` + `auth.txt`) — ошибка exit 2. Выходной файл —
  `<stem>.md`, перезаписывается.
- **Ретраи** — пустой ответ модели или ошибка сервиса повторяются до
  `--max-retries` (по умолчанию 3); после исчерпания попыток — exit 4.
- **Промпт** — содержимое файла передаётся как user message без обязательной
  системной инструкции (системный промпт задаётся в `config` через
  `[ollama] system` опционально); `--system` в CLI переопределяет.
- **Температура** — из конфига (по умолчанию `0.7`), диапазон `[0.0, 2.0]`.

## Docker Compose

- сервис `ollama`: образ `ollama/ollama:latest`, GPU через
  `compose.gpu.yaml` override (NVIDIA, RTX 2070 SUPER), иначе CPU;
  entrypoint поднимает `ollama serve` и докачивает модель из `OLLAMA_MODEL`
  (идемпотентно); healthcheck = `ollama show` — приложение стартует только
  когда модель готова; volume `ollama_models`;
- сервис `app`: сборка из Dockerfile, default command отсутствует (пользователь
  передаёт аргументы при запуске), монтирование `./input` и `./output`,
  `depends_on: ollama: condition: service_healthy`;
- `.env` c `OLLAMA_MODEL=qwen2.5:7b-instruct` — единая точка смены модели;
- приложение ходит на ollama по внутренней сети compose: `http://ollama:11434`.

## Dockerfile

Многоступенчатая сборка: builder на `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`
(sync --frozen --no-install-project), runtime — `python:3.13-slim-bookworm`,
непривилегированный пользователь `app`, `WORKDIR /data`,
`ENTRYPOINT ["python", "-m", "llm_engine"]`.