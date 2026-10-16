# Spec: add-external-defense (security-wall)

## Цель
Внешний контур защиты LLM-агента: `security-wall` — Ollama-совместимый прокси
на FastAPI + Guardrails AI (`guardrails-ai`), стоящий между агентом и локальной
abliterated-моделью. Валидирует входящий prompt (prompt injection, токсичность)
и исходящий ответ (PII, утечка данных), блокирует или маскирует при нарушении.

## Интеграция с агентом
Агент (Go) уже поддерживает `EXTERNAL_GUARD=on`: в этом режиме `LLMBaseURL`
берётся из `WALL_BASE_URL` (`http://wall:5000`) и все запросы `/api/chat`
проксируются через wall. Агент не меняется — wall принимает тот же Ollama-контракт.

## Эндпоинты security-wall

### `GET /healthz` → `ok`
Смоук-проверка что wall жив и Ollama доступен.

### `POST /api/chat` (Ollama-совместимый)
Принимает тело запроса Ollama `/api/chat` (`model`, `messages`, `stream`),
сканирует `content` сообщений, проксирует в Ollama, сканирует ответ.

| Поведение | Условие |
|-----------|---------|
| Блокировка input | `SCAN_INPUT=on` и любой сканер input вернул violation |
| Блокировка output | `SCAN_OUTPUT=on` и сканер output вернул violation |
| Прокси без изменений | Оба флага off, либо сканирование прошло |
| Ответ на блок | JSON `{"error":"blocked by security-wall","reason":...}` с HTTP 403 |

## Валидаторы Guardrails AI
- Input: `DetectPromptInjection` (prompt injection, jailbreak) — при отсутствии пакета валидатор пропускается (passthrough)
- Output: `DetectPII` (PII — имена, email, телефоны, адреса) — при отсутствии пакета валидатор пропускается (passthrough)

### Установка пакетов валидаторов (обязательно для блокировки угроз)

Базовый пакет `guardrails-ai` содержит только ядро `Guard`. Специализированные
валидаторы — отдельные пакеты, берутся из **PyPI** (https://pypi.org):

| Угроза | Валидатор | Пакет на PyPI | Команда |
|--------|-----------|---------------|---------|
| Prompt injection / jailbreak | `DetectPromptInjection` | `guardrails-ai-detect-prompt-injection` | `pip install guardrails-ai-detect-prompt-injection` |
| PII (имена, email, телефоны, адреса) | `DetectPII` | `guardrails-ai-detect-pii` | `pip install guardrails-ai-detect-pii` |

Без этих пакетов `security-wall` работает в режиме **passthrough** (прокси без
блокировки). Чтобы блокировка заработала, установите оба пакета в окружении/образе `wall`:

```bash
# локально / в контейнере wall
pip install guardrails-ai-detect-prompt-injection guardrails-ai-detect-pii
```

В `dz5/security-wall/Dockerfile` зависимости ставятся из `requirements.txt`
(`pip install --no-cache-dir -r requirements.txt`), поэтому достаточно добавить
оба пакета в `requirements.txt` и пересобрать образ:

```bash
cd dz5/security-wall
DOCKER_BUILDKIT=1 docker build -t dz5-security-wall:local .
```

## Конфигурация (env)
| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Куда проксировать чат |
| `WALL_LISTEN` | `:5000` | Порт wall |
| `SCAN_INPUT` | `on` | Сканировать входящий промпт |
| `SCAN_OUTPUT` | `on` | Сканировать ответ LLM |
| `BLOCK_ON_FAIL` | `on` | Блокировать при violation (иначе — предупреждение в теле ответа) |

## Образ
База: `python:3.12-slim` (локально проверен; `ohw-python:3.13` из Dz4 не
извлекается из-за ошибки Docker-слоя `0xc000013a` на этой машине).
Зависимости: `fastapi`, `uvicorn`, `guardrails-ai`, `httpx`. Сборка:
`dz5/security-wall/Dockerfile`, образ `dz5-security-wall:local`.

## Состав сервиса в compose (`dz5/infra/compose.yaml`)
- Сервис `wall`, контейнер `dz5-wall`, порт `5000`, `depends_on: ollama`
  (condition: service_healthy), env `OLLAMA_BASE_URL=http://ollama:11434`,
  `SCAN_INPUT=on`, `SCAN_OUTPUT=on`, `BLOCK_ON_FAIL=on`.
- L3-лимиты: `deploy.resources.limits.memory: 1024M`.
- Сеть `ib-network`.

## Preset matrix (project.md §Архитектура защиты)
| Пресет | BUILTIN_GUARDS | EXTERNAL_GUARD | Агент ходит |
|--------|----------------|----------------|-------------|
| 1 Baseline | off | off | Прямо в Ollama |
| 2 Встроенная | on | off | Прямо в Ollama |
| 3 Только внешняя | off | on | Через wall:5000 |
| 4 Full Defense | on | on | Через wall:5000 |

## Definition of done
- [x] Spec written (этот файл)
- [ ] `dz5/security-wall/` — FastAPI + Guardrails AI, `/api/chat` прокси, `/healthz`
- [ ] `dz5/security-wall/Dockerfile` на `ohw-python:3.13`, сборка зелёная
- [ ] Сервис `wall` в `dz5/infra/compose.yaml`, поднимается через `docker compose up -d wall`
- [ ] `EXTERNAL_GUARD=on` агент ходит через wall и получает ответ
- [ ] Wall блокирует инъекцию в input (пример: `Ignore previous instructions`)
- [ ] tasks.md галочки зелёные; коммит только в `dz5/`