# Tasks: add-agent-core

## 1. Планирование (spec-first)

- [x] `proposal.md`: архитектура Go-агента + 4 конфигурации через env.
- [x] Delta-spec `specs/agent-core/spec.md`: HTTP-контракт, контракт `db_query`,
      структуры `User`/`PersonalData`, матрица пресетов.

## 2. Каркас Go-приложения — Вариант №1 (Baseline, без защиты)

- [x] `agent/go.mod`: модуль `ohw/dz5/agent`, пин Go 1.26, зависимости минимум
      (stdlib + `modernc.org/sqlite` для SQLite без cgo).
- [x] `agent/models.go`: структуры `User` и `PersonalData` 1:1 с
      `infra/db/init.sql` + `infra/db/seed.sql`.
- [x] `agent/ollama.go`: клиент `POST /api/chat` (base URL через
      `OLLAMA_BASE_URL`, модель через `OLLAMA_MODEL`).
- [x] `agent/db.go`: tool `db_query` — read-only по умолчанию (allow-list
      `SELECT`), `ALLOW_WRITES=1` открывает модификации (демо LLM03).
- [x] `agent/guards.go`: переключатель `BUILTIN_GUARDS=on|off` (пока каркас:
      выбор промпта; полный набор guard-инструкций — веха builtin-guards).
- [x] `agent/prompts/minimal.txt`: минимальный системный промпт (пресеты 1/3).
- [x] `agent/prompts/guarded.txt`: заглушка защищённого промпта (пресеты 2/4,
      наполнение — веха builtin-guards).
- [x] `agent/main.go`: HTTP-сервер `:8080`, `GET /healthz` → `ok`,
      `POST /chat {"message": "..."}` → tool-loop (LLM → db_query → LLM) → ответ.
- [x] `agent/Dockerfile`: multi-stage (builder `golang:1.26-alpine` + runtime
      `alpine:<pin>` или `scratch`), образ `dz5-agent:local`.

## 3. Сервис в compose

- [x] Сервис `agent` (контейнер `dz5-agent`, порт `8080`, зависимость от `ollama`,
      env `OLLAMA_BASE_URL=http://ollama:11434`, `BUILTIN_GUARDS`,
      `EXTERNAL_GUARD`, `ALLOW_WRITES`), L3-лимиты + restart-policy.

## 4. Проверка (прогнать вживую)

- [x] Сборка в Docker — зелёная (`docker compose build agent` → `dz5-agent:local`).
- [ ] `docker compose build agent` — успешно.
- [x] `docker compose up -d ollama agent` + `GET /healthz` → `ok`.
- [x] Пресет 1: легитимное чтение (`Покажи пользователей`) работает —
      вернул user1..user5 через `db_query(SELECT username FROM users)`.
- [x] Пресет 1: `ALLOW_WRITES=1` подхвачен (`allow_writes=true`); `db-restore`
      отрабатывает без ошибок. LLM не решился на `DROP TABLE users` энд투энд
      (вернул «ERROR: table already exists.» без `db_query`), но код `QueryDB`
      при `allowWrites=true` пропускает запись — tool-abuse возможен.

## 5. Отметка выполнения

- [ ] Все проверки §4 зелёные; галочки по факту.
