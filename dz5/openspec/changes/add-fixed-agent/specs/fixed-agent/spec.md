# Spec: fixed-agent (preset 5)

## ADDED Requirements

### Requirement: Реальный chat и tool-loop

`agent/fixed/` MUST обрабатывать `POST /chat` с JSON `{"message": "..."}` и
возвращать:

```json
{
  "reply": "...",
  "agent": "fixed",
  "preset": 5,
  "tool_calls": [{"sql": "SELECT ...", "rows": 1}]
}
```

Агент MUST вызывать Ollama (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`), выполнять
`db_query` через SQLite и возвращать финальный ответ. Echo-заглушка MUST быть
удалена.

#### Scenario: Легитимный запрос

- **WHEN** клиент отправляет `{"message":"How many users are in the database?"}`
- **THEN** агент выполняет разрешённый `SELECT count(*)` и возвращает `reply`
  и `tool_calls`

#### Scenario: LLM недоступна

- **WHEN** Ollama возвращает ошибку
- **THEN** клиент получает `502`, без фиктивного `reply`

### Requirement: Prompt isolation

`agent/fixed/prompts/fixed.txt` MUST содержать strict-политику:

- запрет раскрытия системного промпта и hidden context;
- запрет смены роли и выполнения инструкций из пользовательских данных;
- запрет деструктивного SQL и вывода секретов/PII;
- контракт `db_query`: один `SELECT`, известные таблицы, explicit columns или
  агрегат.

Данные инструмента MUST передаваться модели как untrusted data. Пустой или
отсутствующий prompt — fatal error при старте.

### Requirement: Input guards

До вызова LLM fixed-агент MUST блокировать:

- instruction override / jailbreak / roleplay;
- попытки раскрыть system prompt или hidden context;
- encoding-обходы (base64, rot13, zero-width);
- подозрительные URL.

Блокировка — `403`, `blocked=true`, LLM не вызывается.

### Requirement: Output guards

После ответа LLM fixed-агент MUST блокировать:

- секреты (`password`, `api_token`, `secret`, `token`);
- PII (email, телефон, паспорт, ИНН, СНИЛС, карта);
- признаки утечки system prompt / hidden context.

При срабатывании — `403`; сырой ответ не отдаётся клиенту.

### Requirement: Tool whitelist

`db_query` MUST разрешать только одиночный `SELECT` по таблицам `users`,
`personal_data`, `clients`, `orders`, `documents`.

Запрещены:

- `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `ATTACH`, `DETACH`,
  `PRAGMA`, `VACUUM`, `CREATE`, `REPLACE`, `TRUNCATE`;
- `UNION`, stacked statements, `sqlite_master`, `sqlite_schema`, `secrets`;
- `SELECT *` (нужны explicit columns или агрегат).

Записи в БД MUST быть запрещены всегда, независимо от env.

#### Scenario: DROP TABLE

- **WHEN** LLM предлагает `DROP TABLE users`
- **THEN** запрос не исполняется, `tool_calls` не содержит его, БД остаётся
  целой

#### Scenario: SELECT *

- **WHEN** LLM предлагает `SELECT * FROM personal_data`
- **THEN** запрос отклоняется, модель получает ошибку политики

### Requirement: Immutable settings

- `GET /status` MUST возвращать `agent=fixed`, `preset=5`,
  `builtinGuards=true`, `externalGuard=false`, `allowWrites=false`,
  `runtimeToggles=false`, `immutable=true`.
- `POST /settings` MUST возвращать `403` при любой попытке изменения.
- UI показывает для fixed disabled-переключатели.

#### Scenario: Попытка включить writes

- **WHEN** клиент отправляет `POST /settings` с `allowWrites=true`
- **THEN** ответ `403`, состояние не меняется

### Requirement: Observability

- лог `/chat`: preset, guards, blocked, reason, tool calls, latency;
- секреты и тела PII в лог не пишутся;
- `/healthz` возвращает `200 ok` без зависимости от LLM/БД.

### Requirement: Container и shared core

- fixed-агент MUST использовать общее ядро
  `agent/internal/agentcore/`, а не копию baseline.
- `agent/fixed/go.mod` MUST отсутствовать; fixed собирается из модуля
  `ohw/dz5/agent`.
- `agent/Dockerfile` MUST поддерживать build arg для сборки fixed.
- В compose fixed слушает `8081:8080`, read-only mount БД, memory limit `512M`,
  restart `unless-stopped`.

## Definition of done

- [x] Реальный tool-loop, echo удалён.
- [x] Prompt isolation и guards работают.
- [x] Tool whitelist и write-block работают.
- [x] Immutable settings: `/settings` → `403`.
- [x] `go test ./agent/...`, `go vet ./agent/...` зелёные.
- [x] Live smoke: легитимный запрос, инъекция, `DROP`.
- [x] README/red-team не заявляют больше, чем подтверждено.
