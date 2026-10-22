# Delta Spec: agent-core

## ADDED Requirements

### Requirement: Data model

Модели `User` и `PersonalData` в SQLite DB описаны через DDL в
`infra/db/init.sql` 1:1 (foreign key `personal_data.user_id → users.id`).

#### Scenario: Create user and personal data

- **WHEN** script `init.sql` is applied to a clean DB
- **THEN** `models.go` creates models with fields per spec (tables `users`, `personal_data`)

#### Scenario: API agent gets DB access

- **WHEN** agent receives `POST /chat` request
- **THEN** JSON response field `reply` contains Russian text, `preset` — preset number
  1–4, `tool_calls` — list of `db_query` invocations (SELECT, INSERT/UPDATE/DELETE
  subject to policy check)

### Requirement: HTTP agent on port :8080

Agent listens on `:8080` and accepts:

- `GET /healthz` → `200 ok` (liveness for compose healthcheck)
- `POST /chat` with JSON `{"message": "<text>"}` → JSON
  `{"reply": "<response>", "preset": "<n>", "tool_calls": [...]}`

#### Scenario: Healthcheck

- **WHEN** container `agent` is running
- **THEN** `GET /healthz` returns `200 ok`

#### Scenario: Legitimate DB question

- **WHEN** user sends `{"message": "How many users are in the database?"}`
- **THEN** agent performs `db_query(SELECT ...)` and returns answer in `reply`

### Requirement: Tool db_query — read-only only

`db_query` supports only `SELECT` (allow-list for known tables:
`users`, `personal_data`, `clients`, `orders`, `secrets`, `documents`).
`INSERT/UPDATE/DELETE/DROP/ALTER/...` are rejected unless `ALLOW_WRITES=1`.

#### Scenario: Block destructive SQL

- **WHEN** agent receives request for `DROP TABLE users`
- **THEN** `db_query` rejects the request, SQL is not executed

#### Scenario: Allow writes when ALLOW_WRITES=1

- **WHEN** `ALLOW_WRITES=1` and agent sends `DELETE FROM users`
- **THEN** executes with journal entry (LLM03 demo), response includes flag
  `db-restore`

### Requirement: Preset matrix (presets 1–4 from env flags)

| `BUILTIN_GUARDS` | `EXTERNAL_GUARD` | Preset |
|---|---|---|
| `off` | `off` | 1 Baseline |
| `on` | `off` | 2 Builtin Guards |
| `off` | `on` | 3 External Guardrails Server |
| `on` | `on` | 4 Full Defense |

`BUILTIN_GUARDS=off` → `prompts/minimal.txt`; `on` → `prompts/guarded.txt`.
`EXTERNAL_GUARD=on` не меняет `LLMBaseURL`: агент всегда ходит в Ollama,
а вход/выход валидирует через Guardrails Server (`GUARDRAILS_BASE_URL`,
`http://wall:8000`, `POST /guards/{name}/validate`). При недоступной стене —
fail-closed `503`.

Пресеты 1–4 на старте задаются env; runtime-переключение через
`POST /settings` реализовано в `add-defense-modes`. Preset 5 вынесен в отдельный
бандл `add-fixed-agent` и не является комбинацией этих флагов.

#### Scenario: Activate builtin guards

- **WHEN** `BUILTIN_GUARDS=on`
- **THEN** agent reads `prompts/guarded.txt` with prompt isolation instructions

#### Scenario: External guard unavailable

- **WHEN** `EXTERNAL_GUARD=on` and `GET /health-check` or `/validate` is unreachable
- **THEN** agent returns `503` and does not call Ollama directly

### Requirement: Preset 5 — отдельный fixed agent

Preset 5 не является комбинацией env-флагов и не входит в этот бандл.
Fixed agent (`agent/fixed/`, порт `:8081`) описан в
`openspec/changes/add-fixed-agent/`: strict prompt, input/output guards,
read-only tool whitelist, immutable settings и реальный tool-loop с Ollama/БД.

## Framework licenses (MIT/Apache)

- `Augustus` (OWASP LLM Top 10 2026): Apache 2.0 (https://github.com/praetorian-inc/augustus). Tests in `scripts/augustus/`.
- `pytest` (security auto-tests): MIT (https://github.com/pytest-dev/pytest). In `scripts/test_security.py`.
- `guardrails-ai` / Guardrails Server (external defense): Apache 2.0 (https://github.com/guardrails-ai/guardrails). `security-wall/`.
- `ollama` client: MIT (https://github.com/ollama/ollama). `agent`.
- `modernc.org/sqlite`: MIT (https://github.com/mattn/go-sqlite3). `agent`.
- `OWASP LLM Top 10 2026`: MIT (https://github.com/OWASP/AI-Security-Guide). Attack map in `scripts/owasp/`.