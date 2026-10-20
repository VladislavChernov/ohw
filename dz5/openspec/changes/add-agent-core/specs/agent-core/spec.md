# Delta Spec: agent-core

## ADDED Requirements

### Requirement: Protective mechanisms for Fixed Agent (preset 5)

Protective mechanisms `User` and `PersonalData` in SQLite DB are described via DDL in
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

### Requirement: Preset matrix (4 presets from env flags)

| `BUILTIN_GUARDS` | `EXTERNAL_GUARD` | Preset |
|---|---|---|
| `off` | `off` | 1 Baseline |
| `on` | `off` | 2 Builtin Guards |
| `off` | `on` | 3 External Wall (security-wall, port 5000) |
| `on` | `on` | 4 Full Defense |

`BUILTIN_GUARDS=off` → `prompts/minimal.txt`; `on` → `prompts/guarded.txt`.
`EXTERNAL_GUARD=on` → LLM via `WALL_BASE_URL` instead of `OLLAMA_BASE_URL`
(in preset 3/4 wall is intermediary; `BLOCK_ON_FAIL=on` on violation).

#### Scenario: Activate builtin guards

- **WHEN** `BUILTIN_GUARDS=on`
- **THEN** agent reads `prompts/guarded.txt` with prompt isolation instructions

### Requirement: Preset 5 — Fixed Agent (agent-fixed)

Fixed Agent (`agent-fixed/`) — separate protected version with:

- Prompt isolation: `<<POLICY>>` / `<<USER>>` / `<<DATA>>` in `prompts/fixed.txt`
- Input/output validation via built-in regex guards
- Tool whitelist: only `SELECT`, writes blocked without `ALLOW_WRITES=1`
- `GET /status` — JSON with protection status
- `GET /ui` — HTML page with toggles for `BUILTIN_GUARDS`, `EXTERNAL_GUARD`, `ALLOW_WRITES`
- `agent-fixed/Dockerfile` — `golang:1.26-alpine` → `scratch`, image `agent-fixed:local`
- `agent-fixed/main.go` — built-in guards: input/output validation, tool policy
- `agent-fixed/prompts/fixed.txt` — protected prompt with isolation
- `agent-fixed/go.mod` — dependency `modernc.org/sqlite`
- `scripts/test_security.py` (MIT) — 15+ payloads: encodings, paraphrase, multi-step injections
- `scripts/augustus/` (Apache 2.0) — requests to Augustus for OWASP LLM Top 10 2026
- `README.md` (dz5/) — run, licenses, UI instructions
- `reports/red-team.md` — manual red-team report (6+ attacks, OWASP classes)

## Framework licenses (MIT/Apache)

- `Augustus` (OWASP LLM Top 10 2026): Apache 2.0 (https://github.com/praetorian-inc/augustus). Tests in `scripts/augustus/`.
- `pytest` (security auto-tests): MIT (https://github.com/pytest-dev/pytest). In `scripts/test_security.py`.
- `guardrails-ai` (external wall protection): MIT (https://github.com/guardrails-ai/guardrails). `security-wall/`.
- `FastAPI` + `Uvicorn`: MIT (https://github.com/tiangolo/fastapi). `security-wall/`.
- `ollama` client: MIT (https://github.com/ollama/ollama). `agent`, `security-wall`.
- `modernc.org/sqlite`: MIT (https://github.com/mattn/go-sqlite3). `agent`.
- `OWASP LLM Top 10 2026`: MIT (https://github.com/OWASP/AI-Security-Guide). Attack map in `scripts/owasp/`.