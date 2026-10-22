# Spec: fixed-agent (preset 5)

## ADDED Requirements

### Requirement: Реальный chat и tool-loop

`agent/fixed/` обслуживает `POST /chat` с JSON `{"message": "..."}` и
возвращает:

```json
{
  "reply": "...",
  "preset": 5,
  "tool_calls": [{"sql": "SELECT ...", "rows": 1}],
  "blocked": false,
  "reason": ""
}
```

Агент вызывает Ollama (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`), выполняет
`db_query` при необходимости и возвращает финальный ответ. Echo-заглушка
удаляется.

#### Scenario: Легитимный вопрос

- **WHEN** приходит `{"message":"How many users are in the database?"}`
- **THEN** агент выполняет `SELECT`, возвращает `reply` и `tool_calls`

#### Scenario: LLM недоступна

- **WHEN** Ollama недоступна
- **THEN** ответ `502`/`503`, без выдуманного `reply`

### Requirement: Prompt isolation

`agent/fixed/prompts/fixed.txt` разделяет:

- `<<POLICY>>` — системные инструкции и запреты;
- `<<USER>>` — запрос пользователя;
- `<<DATA>>` — данные БД.

Данные из `<<DATA>>` не могут менять `<<POLICY>>`. Промпт загружается при
старте; пустой/отсутствующий файл — fatal error.

### Requirement: Входные guards

До вызова LLM проверяются:

- instruction override / jailbreak / roleplay;
- попытки раскрыть system prompt или hidden context;
- encoding-обфускация (base64, unicode, zero-width) — по возможности
  нормализовать перед проверкой;
- URL/эксфильтрация.

При нарушении — `403`, `blocked=true`, LLM не вызывается. Регулярки не
считаются полной защитой; в спеке фиксируется, что это demo-уровень.

### Requirement: Выходные guards

После ответа LLM проверяются:

- секреты/токены/пароли;
- PII (email, телефон, паспорт, карта);
- признаки утечки system prompt/hidden context.

При `BLOCK_ON_FAIL=on` — `403`, исходный ответ не отдаётся. При `off` —
маскировка или ответ с `reason`/warning.

### Requirement: Tool whitelist

`db_query` разрешает `SELECT` только по таблицам `users`, `personal_data`,
`clients`, `orders`, `secrets`, `documents`. `INSERT/UPDATE/DELETE/DROP/ALTER/
ATTACH/PRAGMA` запрещены без `ALLOW_WRITES=1`.

#### Scenario: DROP TABLE

- **WHEN** LLM просит `DROP TABLE users`
- **THEN** tool не выполняется, в `tool_calls` нет успешной записи, ответ
  содержит отказ

#### Scenario: Запись при ALLOW_WRITES=1

- **WHEN** `ALLOW_WRITES=1`, пришло подтверждение, LLM просит `DELETE`
- **THEN** запись выполняется, журналируется, БД можно восстановить
  `db-restore`

### Requirement: Runtime settings

`GET /status` возвращает фактическое состояние:

```json
{
  "preset": 5,
  "builtinGuards": true,
  "externalGuard": false,
  "allowWrites": false,
  "inputGuard": "on",
  "outputGuard": "on",
  "blockOnFail": "on"
}
```

`POST /settings` принимает:

```json
{"builtinGuards": true, "externalGuard": false, "allowWrites": false}
```

- `403`, если `ALLOW_RUNTIME_TOGGLES!=on`.
- `400` при неизвестном поле/типе.
- `allowWrites=true` требует `confirmWrites=true`, иначе `400`.
- Изменения применяются атомарно (mutex) и сразу видны в `/status` и `/chat`.
- `externalGuard` для preset 5 всегда `false`; попытка `true` → `400`.

UI (`GET /ui`) вызывает `/settings` и перерисовывает состояние; чекбоксы не
декоративные.

### Requirement: Observability

- Лог на каждый `/chat`: preset, guards, blocked, reason, tool calls, latency.
- Секреты и полные ответы БД в лог не пишутся.
- `/healthz` → `200 ok` без обращения к LLM/БД (liveness).

### Requirement: Container

- `agent/fixed/Dockerfile`: сборка Go, финальный `scratch` (или distroless),
  бинарник + `prompts/fixed.txt`.
- Порт `:8080`, в compose опубликован как `8081:8080`.
- Read-only mount БД; запись только если явно разрешена.
- L3: memory limit `512M`, restart `unless-stopped`.

## Definition of done

- [ ] Реальный tool-loop, echo удалён.
- [ ] Prompt isolation и guards работают.
- [ ] Tool whitelist и `ALLOW_WRITES` проверены.
- [ ] `/settings` и UI меняют поведение без рестарта.
- [ ] `go vet`, unit/integration тесты зелёные.
- [ ] Живой прогон: легитимный запрос, инъекция, `DROP`, PII.
- [ ] README/red-team обновлены по фактам.
