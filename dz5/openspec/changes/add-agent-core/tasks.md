# Tasks: add-agent-core

## 1. Планирование (spec-first)

- [x] `proposal.md`: архитектура Go-агента + 4 конфигурации через env.
- [ ] Delta-spec `specs/agent-core/spec.md`: HTTP-контракт, контракт `db_query`,
      структуры `User`/`PersonalData`, матрица пресетов.

## 2. Каркас Go-приложения — Вариант №1 (Baseline, без защиты)

- [ ] `agent/go.mod`: модуль `ohw/dz5/agent`, пин Go, зависимости минимум
      (stdlib + `modernc.org/sqlite` для SQLite без cgo).
- [ ] `agent/models.go`: структуры `User` и `PersonalData` 1:1 с
      `infra/db/init.sql` + `infra/db/seed.sql`.
- [ ] `agent/ollama.go`: клиент `POST /api/chat` (base URL через
      `OLLAMA_BASE_URL`, модель через `OLLAMA_MODEL`).
- [ ] `agent/db.go`: tool `db_query` — read-only по умолчанию (allow-list
      `SELECT`), `ALLOW_WRITES=1` открывает модификации (демо LLM03).
- [ ] `agent/guards.go`: переключатель `BUILTIN_GUARDS=on|off` (пока каркас:
      выбор промпта; полный набор guard-инструкций — веха builtin-guards).
- [ ] `agent/prompts/minimal.txt`: минимальный системный промпт (пресеты 1/3).
- [ ] `agent/prompts/guarded.txt`: заглушка защищённого промпта (пресеты 2/4,
      наполнение — веха builtin-guards).
- [ ] `agent/main.go`: HTTP-сервер `:8080`, `GET /healthz` → `ok`,
      `POST /chat {"message": "..."}` → tool-loop (LLM → db_query → LLM) → ответ.
- [ ] `agent/Dockerfile`: multi-stage (builder `golang:<pin>` + runtime
      `alpine:<pin>` или `scratch`), образ `dz5-agent:local`.

## 3. Сервис в compose

- [ ] Сервис `agent` (контейнер `dz5-agent`, порт `8080`, зависимость от `ollama`,
      env `OLLAMA_BASE_URL=http://ollama:11434`, `BUILTIN_GUARDS`,
      `EXTERNAL_GUARD`, `ALLOW_WRITES`), L3-лимиты + restart-policy.

## 4. Проверка (прогнать вживую)

- [ ] `go vet ./...` (или сборка в Docker, т.к. Go на хосте нет) — зелёный.
- [ ] `docker compose build agent` — успешно.
- [ ] `docker compose up -d agent` + `GET /healthz` → `ok`.
- [ ] Пресет 1: легитимное чтение (`Покажи пользователей`) работает.
- [ ] Пресет 1: tool-abuse (`DROP TABLE users`) проходит; `db-restore`
      возвращает состояние (users=5, PII=5).

## 5. Отметка выполнения

- [ ] Все проверки §4 зелёные; галочки по факту.
