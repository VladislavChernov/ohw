# Delta Spec: agent-core

## ADDED Requirements

### Requirement: Структуры данных агента соответствуют схеме БД

Структуры `User` и `PersonalData` ДОЛЖНЫ повторять таблицы `users` и
`personal_data` из `infra/db/init.sql` 1:1 (имена полей, типы, связи
`personal_data.user_id → users.id`).

#### Scenario: Совпадение со схемой

- **WHEN** схема `init.sql` меняется (новая колонка)
- **THEN** структуры в `models.go` обновляются синхронно (иначе spec нарушен)

#### Scenario: API-контракт ответа чата

- **WHEN** агент обрабатывает `POST /chat`
- **THEN** в JSON-ответе поле `reply` содержит финальный текст,
  `preset` — номер конфигурации 1–4, `tool_calls` — список выполненных
  запросов `db_query` (пустой, если tool не вызывался)

### Requirement: HTTP-контракт агента

Агент ДОЛЖЕН слушать `:8080` и предоставлять:

- `GET /healthz` → `200 ok` (liveness для compose-healthcheck и смоука).
- `POST /chat` с JSON `{"message": "<текст>"}` → JSON
  `{"reply": "<ответ>", "preset": "<n>", "tool_calls": [...]}`.

#### Scenario: Healthcheck

- **WHEN** сервис `agent` поднят
- **THEN** `GET /healthz` возвращает `200 ok`

#### Scenario: Легитимный чат

- **WHEN** пресет 1 получает `{"message": "Покажи пользователей"}`
- **THEN** агент выполняет `db_query(SELECT ...)` и возвращает строки в `reply`

### Requirement: Tool db_query read-only по умолчанию

`db_query` ДОЛЖЕН принимать только `SELECT` (allow-list по первому ключевому
слову, регистронезависимо). `INSERT/UPDATE/DELETE/DROP/ALTER/...` ДОЛЖНЫ
отклоняться с ошибкой, если `ALLOW_WRITES != 1`.

#### Scenario: Отклонение модификации по умолчанию

- **WHEN** агент в режиме по умолчанию получает `DROP TABLE users`
- **THEN** `db_query` возвращает ошибку, таблица не изменяется

#### Scenario: Модификация в режиме демонстрации

- **WHEN** `ALLOW_WRITES=1` и агент выполняет `DELETE FROM users`
- **THEN** операция исполняется (демо LLM03), состояние возвращается через
  `db-restore`

### Requirement: Матрица 4 пресетов через env

Поведение агента ДОЛЖНО определяться двумя флагами:

| `BUILTIN_GUARDS` | `EXTERNAL_GUARD` | Пресет |
|---|---|---|
| `off` | `off` | 1 Baseline |
| `on` | `off` | 2 Только встроенная |
| `off` | `on` | 3 Только внешняя (через `security-wall`, веха 4) |
| `on` | `on` | 4 Full Defense |

`BUILTIN_GUARDS=off` → системный промпт `prompts/minimal.txt`;
`on` → `prompts/guarded.txt`. `EXTERNAL_GUARD=on` → клиент LLM ходит в
`WALL_BASE_URL` вместо `OLLAMA_BASE_URL` (в вехе 3 wall отсутствует —
значение `on` без wall ДОЛЖНО возвращать понятную ошибку).

#### Scenario: Переключение промпта

- **WHEN** `BUILTIN_GUARDS=on`
- **THEN** агент стартует с `prompts/guarded.txt` (видно в логе старта)
