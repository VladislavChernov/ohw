# Spec: agent-registry-ui

## ADDED Requirements

### Requirement: Отдельный UI-сервис

UI MUST быть отдельным Go-сервисом `ui/` (порт `:8090`), а не HTML внутри
агентов. Сервис MUST использовать только стандартную библиотеку Go и не
требовать CORS: браузер обращается только к UI-сервису.

### Requirement: Реестр агентов

UI MUST хранить статический реестр агентов из env `AGENT_REGISTRY`:

```text
baseline=http://agent:8080,fixed=http://agent-fixed:8080
```

Реестр MUST валидироваться при старте: непустые уникальные id, абсолютные
`http/https` URL. Неизвестный id в запросе — `404`. Браузер MUST NOT получать
внутренние URL агентов.

`GET /api/agents` MUST возвращать JSON-массив:

```json
[
  {"id":"baseline","name":"Baseline (presets 1-4)","description":"..."},
  {"id":"fixed","name":"Fixed (preset 5)","description":"..."}
]
```

### Requirement: Reverse-proxy

UI MUST проксировать запросы к выбранному агенту:

| Метод | Путь UI | Куда |
|---|---|---|
| GET | `/api/agents/{id}/status` | `GET {agent}/status` |
| POST | `/api/agents/{id}/settings` | `POST {agent}/settings` |
| POST | `/api/agents/{id}/chat` | `POST {agent}/chat` |

Правила:

- path/query кроме известного суффикса не пробрасываются;
- тело и `Content-Type` сохраняются;
- к `/settings` добавляется `X-Settings-Source: ui`;
- статус ответа и тело агента возвращаются без подмены (включая `400/403/503`);
- таймаут прокси MUST быть ограничен (не более 180s);
- ошибка соединения с агентом — `502`; неизвестный агент — `404`.

#### Scenario: Fixed settings

- **WHEN** UI отправляет `POST /api/agents/fixed/settings`
- **THEN** UI получает от fixed-агента `403` и возвращает браузеру `403`

### Requirement: HTML UI

`GET /` и `GET /ui` MUST отдавать страницу с:

- selector агентов из `/api/agents`;
- отображением `/status`;
- переключателями `builtinGuards`, `externalGuard`, `allowWrites`;
- полем чата и выводом `reply`/`tool_calls`/ошибки;
- индикацией `runtimeToggles=false` и disabled-переключателями для fixed.

Страница MUST NOT содержать встроенные адреса контейнеров.

### Requirement: Compose

`infra/compose.yaml` MUST добавить сервис `ui`:

- build context `../ui`, image `dz5-ui:local`;
- порт `8090:8090`;
- `AGENT_REGISTRY` с `agent:8080` и `agent-fixed:8080`;
- healthcheck `GET /healthz`;
- L3 memory limit `128M`, restart `unless-stopped`;
- depends_on `agent`, `agent-fixed` без обязательного healthy (UI должен
  запускаться, даже если один агент ещё стартует).

#### Scenario: Переключение агента

- **WHEN** пользователь выбирает `fixed` в UI
- **THEN** status/chat идут через `/api/agents/fixed/*`, а переключатели
  показываются disabled
