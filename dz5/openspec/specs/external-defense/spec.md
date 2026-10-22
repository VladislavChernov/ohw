# Spec: add-external-defense (Guardrails Server)

## Goal

Внешний контур защиты LLM-агента: отдельный контейнер **Guardrails Server**
(`guardrails-api`) на `:8000`. Стена валидирует вход пользователя и выход
модели через guards, описанные в `security-wall/config.py`, и не проксирует
Ollama.

Решение 2026-10-21: ручной FastAPI-прокси `security-wall/main.py`
(`POST /api/chat`, порт `5000`) **отменён**. Он смешивал проксирование LLM и
валидацию, требовал доступа к Ollama и не давал стандартного API Guardrails.
Новый контракт — REST `POST /guards/{name}/validate`.

## Архитектура

```
user ──> agent (Go, :8080)
           │  input text
           ├──> wall (:8000) POST /guards/input-defense/validate
           │       └── DetectPromptInjection
           │
           ├──> Ollama (:11434) /api/chat
           │
           └──> wall (:8000) POST /guards/output-privacy/validate
                   └── DetectPII
```

- В пресетах 3/4 (`EXTERNAL_GUARD=on`) агент обязан проверить вход **до**
  вызова LLM и выход **после** него.
- Стена не знает про Ollama и не зависит от неё.
- При недоступности стены агент работает **fail-closed**: `503`, запрос не
  уходит в LLM. Это важнее «доступности любой ценой» для ИБ-стенда.
- Go-агент не импортирует Python SDK: только HTTP + JSON. Это исключает
  vendor lock и позволяет заменить реализацию стены.

## Guards (`security-wall/config.py`)

```python
from guardrails import Guard, OnFailAction
from guardrails_ai.detect_prompt_injection import DetectPromptInjection
from guardrails_ai.detect_pii import DetectPII

input_guard = Guard(id="input-defense", name="input-defense").use(
    DetectPromptInjection(check_vector=False, on_fail=OnFailAction.NOOP),
)

output_guard = Guard(id="output-privacy", name="output-privacy").use(
    DetectPII(
        pii_entities=["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON", "CREDIT_CARD"],
        on_fail=OnFailAction.FIX,
    ),
)
```

Имена guards (`input-defense`, `output-privacy`) — контракт с агентом; они
задаются также в env агента (`INPUT_GUARD_NAME`, `OUTPUT_GUARD_NAME`), чтобы
переименование не требовало пересборки Go-кода. В URL используется `id`
guard'а; `Guard` без явного `id` получает UUID, поэтому `id` задаётся явно.

`on_fail=NOOP` для входа означает: сервер отвечает HTTP 200 с
`validationPassed=false`, а агент сам применяет fail-closed политику (`403`).
`on_fail=FIX` для выхода позволяет замаскировать PII и вернуть
`validatedOutput`; при `BLOCK_ON_FAIL=on` агент вместо маскировки отвечает
`403`.

## API Guardrails Server

### `GET /health-check`

Возвращает `{"status": 200, "message": "Ok"}`. Проверяет живость процесса и
(если включён Postgres) доступность БД; Ollama не проверяет, потому что стена
от неё не зависит. Healthcheck в compose должен проверять HTTP 200, а не
строку `ok`.

### `GET /guards`

Список загруженных guards. Используется для smoke-проверки, что `config.py`
прочитан.

### `POST /guards/{id}/validate`

Запрос:

```json
{"llm_output": "text to validate"}
```

Ответ (200) — `ValidationOutcome` (camelCase-алиасы):

```json
{
  "callId": "...",
  "validationPassed": true,
  "validatedOutput": "text to validate",
  "rawLlmOutput": "text to validate"
}
```

При нарушении `validationPassed=false`, а `validatedOutput` содержит
исправленный/исходный текст в зависимости от `on_fail`. Важное исключение:
`on_fail=fix` (PII) возвращает `validationPassed=true` и уже исправленный
`validatedOutput`; факт редактирования виден только по
`validatedOutput != rawLlmOutput`. Ошибки конфигурации или валидатора могут
приходить как HTTP 4xx/5xx. Агент не полагается на наличие отдельного поля
`error`: достаточно `validationPassed != true`, изменения текста или
HTTP-ошибки.

### `POST /guards/{name}/openai/v1/chat/completions`

Стандартный OpenAI-совместимый endpoint Guardrails Server. Агентом **не
используется** (агент вызывает `/validate` и сам ходит в Ollama), но остаётся
доступным для ручных проверок и внешних клиентов.

## Контракт агента со стеной

### Вход

1. `POST {GUARDRAILS_BASE_URL}/guards/{INPUT_GUARD_NAME}/validate`
   с `{"llm_output": userMessage}`.
2. HTTP 2xx + `validationPassed=true` → продолжить.
3. HTTP 2xx + `validationPassed=false` или HTTP 4xx → `403` с причиной,
   LLM не вызывать.
4. HTTP 5xx, таймаут, ошибка соединения → `503` (fail-closed), LLM не вызывать.

### Выход

1. `POST .../guards/{OUTPUT_GUARD_NAME}/validate` с `{"llm_output": reply}`.
2. HTTP 2xx + `validationPassed=true` + `validatedOutput == rawLlmOutput` →
   вернуть `validatedOutput`.
3. HTTP 2xx + `validationPassed=false` **или**
   `validatedOutput != rawLlmOutput` (PII исправлен, но `validationPassed=true`):
   - `BLOCK_ON_FAIL=on` → `403`, исходный ответ не отдавать;
   - `BLOCK_ON_FAIL=off` → вернуть `validatedOutput`, если он непустой,
     иначе исходный ответ с пометкой `externalGuardWarning`.
4. HTTP 5xx/таймаут → `503` (fail-closed), ответ не отдавать.

### Env агента

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `EXTERNAL_GUARD` | `off` | Включает внешний контур |
| `GUARDRAILS_BASE_URL` | `http://wall:8000` | Адрес Guardrails Server |
| `INPUT_GUARD_NAME` | `input-defense` | Guard для входа |
| `OUTPUT_GUARD_NAME` | `output-privacy` | Guard для выхода |
| `GUARDRAILS_TIMEOUT` | `60s` | Таймаут HTTP-запроса к стене (PII cold start ~36s) |
| `BLOCK_ON_FAIL` | `on` | Блокировать при нарушении выхода |

`WALL_BASE_URL` и порт `5000` больше не используются. `LLMBaseURL` всегда
указывает на Ollama; стена — отдельный вызов валидации.

## Зависимости и образ

Проверено по PyPI на 2026-10-21:

- База: `python:3.12-slim` (совместима с `Requires-Python <3.14,>=3.10`).
- `guardrails-api==0.4.4` — сервер (entry point `guardrails-api`).
- `guardrails-ai==0.11.0` — core, guards, validators (совместим с
  `guardrails-api>=0.10.0`).
- `guardrails-ai-detect-prompt-injection==0.2.1a1`.
- `guardrails-ai-detect-pii==0.1.1a1`.
- `pydantic` версии, совместимой с `guardrails-ai` (не пинить вслепую;
  определить `pip check`).
- `fastapi`/`uvicorn`/`httpx` отдельно не нужны: `guardrails-api` тянет
  `fastapi`/`uvicorn` сам.

Важно: CLI `guardrails-api start` вызывает `uvicorn.run(app, port=port)` без
`host`, то есть слушает `127.0.0.1` и в контейнере недоступен снаружи.
Использовать официальный factory-запуск из `guardrails_api/app.py`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip check
COPY config.py .
ENV GR_CONFIG_FILE_PATH=/app/config.py
ENV GUARDRAILS_LOG_LEVEL=INFO
EXPOSE 8000
CMD ["uvicorn", "--factory", "guardrails_api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000"]
```

`create_app` читает `GR_CONFIG_FILE_PATH`, `GR_ENV_FILE`, `PORT`, `HOST`;
`SELF_ENDPOINT` можно задать явно (`http://wall:8000`).

## Compose

- Сервис `wall`, контейнер `dz5-wall`, порт `8000:8000`.
- `depends_on` у стены **нет** (Ollama не нужна). Агент зависит от `ollama`
  и (для пресетов 3/4) от `wall`.
- Healthcheck: `GET /health-check`.
- L3: `deploy.resources.limits.memory: 2048M` (Presidio/spaCy с общим
  `AnalyzerEngine` ~0.9–1.3 GiB RSS).
- `restart: unless-stopped`.
- Сеть `ib-network`.

## Матрица пресетов

| Пресет | BUILTIN_GUARDS | EXTERNAL_GUARD | Маршрут |
|--------|----------------|----------------|---------|
| 1 Baseline | off | off | Agent → Ollama |
| 2 Builtin | on | off | Agent → Ollama |
| 3 External | off | on | Agent → wall.validate → Ollama → wall.validate |
| 4 Full | on | on | Agent → wall.validate → Ollama → wall.validate |
| 5 Fixed | on | n/a | agent-fixed, отдельный контейнер |

## Definition of done

- [x] Решение о Guardrails Server зафиксировано (этот файл).
- [ ] `security-wall/config.py` с `input-defense` и `output-privacy`.
- [ ] `security-wall/requirements.txt` с проверенными версиями; `pip check` чистый.
- [ ] `security-wall/Dockerfile` собирается; entry point существует.
- [ ] `docker compose up -d wall`; `GET /health-check` → 200.
- [ ] `GET /guards` содержит оба guard.
- [ ] Агент с `EXTERNAL_GUARD=on` проверяет вход и выход; легитимный `/chat` проходит.
- [ ] Инъекция `Ignore previous instructions` → `403` (или `validationPassed=false`).
- [ ] PII на выходе → маскируется или блокируется согласно `BLOCK_ON_FAIL`.
- [ ] Стена остановлена → агент возвращает `503`, LLM не вызывается.
- [ ] Пресеты 3/4 не ломаются; тест контракта зелёный.
- [ ] `tasks.md` отражает фактические проверки; коммит только в `dz5/`.

## Лицензии

- `guardrails-ai` / Guardrails Server: **Apache 2.0**
  (https://github.com/guardrails-ai/guardrails).
- `guardrails-ai-detect-prompt-injection`, `guardrails-ai-detect-pii`: проверить
  лицензии пакетов перед распространением.
- Собственный код `security-wall/`: **MIT**.
