# Дельта спеки: ollama-api-contract

## ADDED Requirements

### Requirement: Контракт Ollama HTTP API

- Система SHALL проверять `GET /api/version`.
- Система SHALL проверять `GET /api/tags`.
- Система SHALL проверять `POST /api/show`.
- Система SHALL проверять `POST /api/chat`.
- Система SHALL проверять `GET /api/ps` как warning, если endpoint
  отсутствует.
- Все запросы SHALL иметь таймаут.
- Базовый URL SHALL браться из `OLLAMA_BASE_URL`/конфига.
- Система SHALL NOT использовать streaming в основных вызовах; `stream`
  SHALL быть `false`.
- Система SHALL NOT автоматически скачивать модель.

#### Scenario: Успешный health

- **WHEN** Ollama доступна и модель установлена
- **THEN** `GET /api/version` возвращает 200 и `version`
- **AND** `GET /api/tags` содержит модель
- **AND** `POST /api/show` возвращает `digest`

#### Scenario: Модель отсутствует

- **WHEN** модели нет в `/api/tags`
- **THEN** `health` возвращает `ok=false`
- **AND** код 3
- **AND** `command_hint` содержит `docker exec ohw-ollama ollama pull llama3`

### Requirement: Схемы ответов

- `GET /api/version` SHALL содержать непустой `version`.
- `GET /api/tags` SHALL содержать `models` — массив объектов с `name`,
  `model`, `digest`, `size`, `details`.
- `POST /api/show` SHALL содержать `digest`, `details`, `model_info`.
- `POST /api/chat` SHALL содержать `message.content` (непустой),
  `done=true`, `eval_count > 0`.
- `format=json` SHALL давать `message.content`, который парсится как JSON.
- Неизвестные поля SHALL NOT ломать разбор.
- Пустой `message.content` SHALL считаться ошибкой `API-04`.

#### Scenario: Пустой ответ

- **WHEN** `/api/chat` вернул 200, но `message.content=""`
- **THEN** проверка `API-04` падает
- **AND** `api-check` возвращает код 4

### Requirement: Параметры генерации

- `POST /api/chat` SHALL принимать `options.temperature`, `options.seed`,
  `options.num_ctx`, `options.num_predict`.
- При `temperature=0`, `seed=7`, `num_predict=16` ответ SHALL иметь
  `eval_count <= 32` (warning при превышении) и `prompt_eval_count > 0`.
- Система SHALL записывать фактические параметры в отчёт.
- Система SHALL NOT считать `seed` гарантией байтовой повторяемости.

#### Scenario: Ограничение длины

- **WHEN** `num_predict=16`
- **THEN** `eval_count` не превышает 32
- **AND** при превышении выставляется warning

#### Scenario: Параметры в отчёте

- **WHEN** выполнен `api-check`
- **THEN** `report.json` содержит `temperature`, `seed`, `num_ctx`,
  `num_predict`

### Requirement: Ошибки API

- Неизвестная модель SHALL давать 404 и непустой `error`.
- Битый JSON SHALL давать 400.
- Таймаут SHALL приводить к `OllamaConnectionError` или
  `OllamaResponseError`, а не к голому `httpx`-исключению.
- `api-check` без Ollama SHALL возвращать код 3.
- `api-check` при провале обязательной проверки SHALL возвращать код 4.
- Провал warning-проверки SHALL NOT менять код на 4.

#### Scenario: Неизвестная модель

- **WHEN** `POST /api/chat {"model": "no-such-model"}`
- **THEN** HTTP 404
- **AND** `error` непустой
- **AND** `API-06` проходит

#### Scenario: Битый JSON

- **WHEN** тело запроса не парсится
- **THEN** HTTP 400
- **AND** `API-07` проходит

#### Scenario: Таймаут

- **WHEN** клиент с `timeout=0.001` вызывает `/api/chat`
- **THEN** поднимается `OllamaConnectionError`/`OllamaResponseError`
- **AND** `API-10` проходит

### Requirement: Параллельные запросы

- Система SHALL поддерживать проверку трёх параллельных `chat`-запросов.
- Ошибка параллельной проверки SHALL быть warning, не fail.
- Система SHALL NOT использовать параллелизм для серий деградации по
  умолчанию.

#### Scenario: Три запроса

- **WHEN** запущены 3 параллельных `chat`
- **THEN** все возвращают 200
- **AND** при ошибке выставляется warning

### Requirement: Тесты API

- Unit-тесты SHALL использовать `httpx.MockTransport` и не ходить в сеть.
- Integration-тесты SHALL быть помечены `integration`.
- Integration-тесты SHALL skip, если Ollama недоступна или модели нет.
- Unit-тесты SHALL покрывать `API-01…API-12`.
- Тесты SHALL NOT вызывать `ollama pull`.

#### Scenario: Unit без Ollama

- **WHEN** запущен `pytest -m "not integration"`
- **THEN** все API-проверки проходят на `MockTransport`

#### Scenario: Integration со стендом

- **WHEN** запущен `pytest -m integration` и стенд доступен
- **THEN** `API-01…API-10` проходят
- **AND** `API-11`/`API-12` могут быть warning
