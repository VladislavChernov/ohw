# Spec: defense-modes (runtime presets 1–4)

## ADDED Requirements

### Requirement: Runtime configuration

Основной агент хранит настройки в структуре под `sync.RWMutex`:

```go
type RuntimeSettings struct {
    BuiltinGuards bool
    ExternalGuard bool
    AllowWrites   bool
}
```

- Env задаёт начальные значения при старте.
- `Preset` вычисляется: `1` (off/off), `2` (on/off), `3` (off/on),
  `4` (on/on).
- Каждый `/chat` берёт снимок настроек и использует его до конца запроса;
  переключение во время запроса не меняет уже начатый запрос.
- `Preset=5` к этому endpoint не относится.

### Requirement: `GET /status`

Возвращает:

```json
{
  "preset": 2,
  "builtinGuards": true,
  "externalGuard": false,
  "allowWrites": false,
  "runtimeToggles": true,
  "toolPolicy": "allow-list: SELECT only, writes blocked without ALLOW_WRITES=1",
  "llmBaseURL": "http://ollama:11434",
  "guardrailsBaseURL": "http://wall:8000"
}
```

`runtimeToggles` показывает, разрешён ли `POST /settings`.

### Requirement: `POST /settings`

Запрос:

```json
{
  "builtinGuards": true,
  "externalGuard": true,
  "allowWrites": false,
  "confirmWrites": false
}
```

Правила:

- `403`, если `ALLOW_RUNTIME_TOGGLES != on`.
- `400` при неизвестном поле, неверном типе или отсутствии обязательного поля.
- `allowWrites=true` требует `confirmWrites=true`; иначе `400` с
  `"confirmWrites required"`.
- `allowWrites=false` не требует подтверждения.
- Успех: `200` с новым `/status`-объектом.
- Изменение атомарное: либо все поля применились, либо ни одно.
- `externalGuard=true` не проверяет стену синхронно; если стена недоступна,
  следующий `/chat` вернёт `503` (fail-closed).

#### Scenario: Включить внешнюю защиту

- **WHEN** `POST /settings {"builtinGuards":false,"externalGuard":true,"allowWrites":false}`
- **THEN** preset становится `3`, `/chat` валидирует вход/выход через wall

#### Scenario: Включить записи без подтверждения

- **WHEN** `allowWrites=true`, `confirmWrites=false`
- **THEN** `400`, настройки не меняются

#### Scenario: Toggles выключены

- **WHEN** `ALLOW_RUNTIME_TOGGLES=off`
- **THEN** `403`, настройки не меняются

### Requirement: Динамический промпт

Оба промпта (`minimal.txt`, `guarded.txt`) загружаются при старте и
проверяются на непустоту. При `builtinGuards=true` используется
`guarded.txt`, иначе `minimal.txt`. Переключение не читает файлы с диска на
каждый запрос.

### Requirement: Внешняя защита и fail-closed

- `externalGuard=false`: агент ходит в Ollama напрямую.
- `externalGuard=true`: вход и выход валидируются через Guardrails Server
  (`GUARDRAILS_BASE_URL`, `POST /guards/{name}/validate`).
- HTTP 5xx/таймаут/connection error → `503`; прямой вызов Ollama в обход
  стены запрещён.
- `BLOCK_ON_FAIL` управляет поведением при нарушении выхода (блок или маска).

### Requirement: Audit log

Каждое успешное и отклонённое изменение пишется в JSONL:

```json
{
  "ts": "2026-10-21T12:00:00Z",
  "event": "settings.update",
  "source": "ui",
  "old": {"builtinGuards": false, "externalGuard": false, "allowWrites": false},
  "new": {"builtinGuards": true, "externalGuard": false, "allowWrites": false},
  "preset": 2,
  "result": "ok"
}
```

Путь: `AUDIT_LOG` (по умолчанию `/data/audit.jsonl`); секреты и тексты
запросов не логируются.

### Requirement: UI

`GET /ui`:

- показывает `preset`, состояние guards и предупреждение, если
  `runtimeToggles=false`;
- чекбоксы вызывают `POST /settings`;
- при `400/403/503` показывает ошибку и перечитывает `/status`;
- отдельный confirm для `allowWrites=true`.

### Requirement: L3

- Compose limits: agent `512M`, wall `2048M`, ollama `4000M`.
- Таймауты: HTTP-клиент стены `GUARDRAILS_TIMEOUT=60s`.
- Wall использует один общий `AnalyzerEngine`/`AnonymizerEngine`; без этого
  `DetectPII` создавал новый spaCy-analyzer на каждый validate и терял
  ~300 MiB на вызов.
- `restart: unless-stopped`.
- L3 не отменяет запросы внутри чужого кода; это ограничитель ресурсов, а не
  замена таймауту.

## Definition of done

- [x] Runtime settings под mutex; снимок на запрос.
- [x] `POST /settings` + `ALLOW_RUNTIME_TOGGLES` + подтверждение записей.
- [x] Динамический промпт без чтения файлов на каждый запрос.
- [x] Внешняя защита fail-closed.
- [x] UI реально переключает; ошибки видны.
- [x] Audit-log пишется.
- [x] Тесты: preset 1–4, 403/400, конкурентность, wall down → 503.
- [x] Живой прогон: переключение без рестарта проверено (P3 + инъекция,
      P1 + `/chat`, P1→P2 в `add-builtin-guards`).
