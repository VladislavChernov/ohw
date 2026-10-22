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
- [x] `agent/guards.go`: переключатель `BUILTIN_GUARDS=on|off`; полный набор
      guard-инструкций — веха `add-builtin-guards` (реализована).
- [x] `agent/prompts/minimal.txt`: минимальный системный промпт (пресеты 1/3).
- [x] `agent/prompts/guarded.txt`: prompt-isolation политика (пресеты 2/4).
- [x] `agent/main.go`: HTTP-сервер `:8080`, `GET /healthz` → `ok`,
      `POST /chat {"message": "..."}` → tool-loop (LLM → db_query → LLM) → ответ.
- [x] `agent/Dockerfile`: multi-stage (builder `golang:1.26-alpine` + runtime
      `alpine:<pin>` или `scratch`), образ `dz5-agent:local`.

## 3. Сервис в compose

- [x] Сервис `agent` (контейнер `dz5-agent`, порт `8080`, зависимость от `ollama`,
      env `OLLAMA_BASE_URL=http://ollama:11434`, `BUILTIN_GUARDS`,
      `EXTERNAL_GUARD`, `ALLOW_WRITES`), L3-лимиты + restart-policy.

## 4. Проверка (прогнать вживую)

- [x] Код агента и сервис в compose добавлены; статическая структура собрана.
- [x] `docker compose build agent` — успешно (`dz5-agent:local`).
- [x] `docker compose up -d ollama agent` + `GET /healthz` → `ok`.
- [x] Пресет 1: легитимный запрос прошёл, `db_query` выполнен
      (`SELECT COUNT(id) FROM users`, `rows=1`); ответ модели «1 user» неверен,
      в БД 5 строк — это артефакт модели, не тула.
- [ ] Пресет 1: `ALLOW_WRITES=1` и `db-restore` проверены живым прогоном.
      Unit `TestQueryDBWriteGate` зелёный; live-модель не выдала исполняемый SQL,
      `db-restore` выполнен.
- [x] `extractSQL`: fenced block с тегом `sql` и без него, raw SQL-префикс.
- [x] Пресеты 2/4: `guarded.txt` выбран; preset 2 ответил «5 users» с
      `SELECT COUNT(id)`, preset 4 ответил 200 (tool call или текстовый ответ).
- [x] `go vet ./agent/...` — зелёный.
- [ ] Результаты сохранены в `reports/<run-id>/` (структуры пока нет).

## 5. Отметка выполнения

- [ ] Все проверки §4 зелёные; галочки по факту. Сейчас живыми прогонами
      закрыты сборка, health, пресеты 1/2/4, `go vet`; остались `ALLOW_WRITES`
      и формат `reports/<run-id>/`.
