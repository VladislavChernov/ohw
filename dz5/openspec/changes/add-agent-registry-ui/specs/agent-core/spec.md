# Spec: agent-core (shared baseline/fixed)

## ADDED Requirements

### Requirement: Общее ядро

`agent/internal/agentcore/` MUST содержать переиспользуемые части обоих
агентов:

- загрузку конфига и промптов;
- `RuntimeSettings`, audit JSONL, `presetOf`;
- Ollama-клиент;
- `db_query` и tool-loop;
- клиент Guardrails Server;
- HTTP-обработчики `/healthz`, `/status`, `/settings`, `/chat`.

Baseline и fixed MUST различаться политикой (`profile`), а не копией
Ollama/SQLite/HTTP-кода.

### Requirement: Baseline-агент (preset 1–4)

`agent/main.go` MUST оставаться entrypoint baseline-агента:

- `/status` и `/settings` отражают runtime settings;
- `/chat` работает как раньше: optional input wall → tool-loop → optional
  output wall;
- `ALLOW_RUNTIME_TOGGLES=on` разрешает `POST /settings`, иначе `403`;
- `ALLOW_WRITES` управляет write-политикой `db_query`;
- встроенный `/ui` MUST быть удалён: UI живёт в отдельном сервисе.

### Requirement: Fixed-агент (preset 5)

`agent/fixed/main.go` MUST быть реальным агентом, а не echo-заглушкой:

- `/chat` вызывает Ollama и выполняет tool-loop `LLM → db_query → LLM`;
- системный prompt загружается из `agent/fixed/prompts/fixed.txt`; пустой или
  отсутствующий файл — fatal;
- `db_query` разрешает только одиночный `SELECT` по известным таблицам;
- `INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/PRAGMA/VACUUM/CREATE/REPLACE` и
  `sqlite_master` MUST быть запрещены всегда;
- записи в БД MUST быть запрещены независимо от env;
- input guards блокируют override/jailbreak/prompt-leak до вызова LLM;
- output guards блокируют утечку секретов/PII после ответа;
- `GET /status` возвращает `preset=5`, `agent=fixed`, `builtinGuards=true`,
  `externalGuard=false`, `allowWrites=false`, `runtimeToggles=false`;
- `POST /settings` возвращает `403` при любой попытке изменения;
- `/healthz` не зависит от Ollama/БД.

### Requirement: Prompt isolation fixed

Fixed prompt MUST разделять политику, пользовательский ввод и данные
инструмента. Данные `db_query` передаются модели как untrusted data, а не как
инструкции. Fixed prompt MUST запрещать раскрытие системного промпта и
деструктивные SQL-операции.

### Requirement: Сборка

- `agent/fixed/go.mod` MUST быть удалён; fixed входит в модуль
  `ohw/dz5/agent`.
- `agent/Dockerfile` MUST поддерживать сборку обоих бинарников через build arg
  (baseline и fixed) без копирования исходников в отдельный контекст.
- `agent-fixed` в compose MUST использовать тот же контекст `../agent`,
  отдельный target/arg и порт `8081:8080`.

#### Scenario: Легитимный запрос fixed

- **WHEN** `POST /chat {"message":"How many users are in the database?"}`
- **THEN** агент вызывает Ollama, выполняет разрешённый `SELECT count(*)` и
  возвращает `reply` + `tool_calls`

#### Scenario: Деструктивный SQL fixed

- **WHEN** LLM предлагает `DROP TABLE users` или `DELETE FROM users`
- **THEN** `db_query` не исполняет запрос, БД остаётся целой, ответ объясняет
  отказ

#### Scenario: Immutable settings fixed

- **WHEN** `POST /settings` с любым телом
- **THEN** ответ `403`, состояние `/status` не меняется
