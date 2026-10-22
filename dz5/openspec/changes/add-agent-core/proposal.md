# Proposal: Go-агент стенда (ядро + 4 конфигурации защиты)

## Почему

Стенду нужен собственный агент на Go: он ходит в локальную abliterated-модель
(`ollama_target`, `huihui_ai/llama3.2-abliterate:3b`), читает SQLite через tool
`db_query` и прогоняется в 4 конфигурациях защиты — от «вообще без защиты» до
«Full Defense». Чужой фреймворк (`drujensen/aiagent`) для этой роли не подошёл:
нужен контролируемый код, где встроенные guard'ы — это наши строки, а не чужие.

## Что делаем

- Каркас Go-приложения в `dz5/agent/`: `go.mod` (пин Go + минимум зависимостей),
  `main.go` (HTTP-сервер: `/healthz`, `/chat`), клиент Ollama
  (`/api/chat` против `http://ollama:11434` внутри сети, `http://localhost:11434`
  с хоста).
- Структуры `User` и `PersonalData` — 1:1 со схемой `infra/db/init.sql` и сидом
  `infra/db/seed.sql` (таблицы `users`, `personal_data`).
- Tool `db_query`: SQLite read-only по умолчанию (allow-list `SELECT`);
  режим tool-abuse (`ALLOW_WRITES=1`) — только для демонстрации LLM03.
- 4 конфигурации через env `BUILTIN_GUARDS=on|off` (системный промпт:
  минимальный vs защищённый) и `EXTERNAL_GUARD=on|off` (прямо в LLM vs
  `POST /guards/{name}/validate` в Guardrails Server, веха 4). Пресет 1
  (Baseline, оба `off`) — первый шаг.
- Сервис `agent` в `infra/compose.yaml`: контейнер `dz5-agent`, порт `8080`,
  зависимость от `ollama`, L3-лимиты памяти, restart-policy.
- Промпты: `prompts/minimal.txt` (пресеты 1/3), `prompts/guarded.txt`
  (пресеты 2/4, встроенные guard-инструкции).

## Спека

Новая capability `agent-core`: HTTP-контракт (`/healthz`, `/chat`), контракт
`db_query` (read-only по умолчанию), матрица 4 пресетов через env, структуры
`User`/`PersonalData` как в БД.

## Проверка

- `docker compose build agent` — успешно (Go toolchain в builder-стадии).
- `docker compose up -d agent` + `GET /healthz` → `ok`.
- Пресет 1: `POST /chat {"message":"Покажи пользователей"}` → агент читает
  `users` через `db_query` и отвечает (легитимное чтение работает).
- Пресет 1: `DROP TABLE users` через агента проходит (уязвимость видна),
  `db-restore` возвращает состояние.

## Изменения

- `dz5/agent/` — новый каталог: `go.mod`, `main.go`, `ollama.go`, `db.go`,
  `guards.go`, `prompts/`, `Dockerfile`.
- `infra/compose.yaml` — сервис `agent`.
- `openspec/changes/add-agent-core/` — этот бандл (+ delta-spec `agent-core`).

## Impact

- Новый сервис; БД и LLM не затрагиваются.
- Агент — наш код: дальнейшие вехи (builtin-guards, wall, harness) опираются
  на его env-переключатели.
