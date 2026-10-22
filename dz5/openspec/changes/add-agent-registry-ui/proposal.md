# Proposal: add-agent-registry-ui

## Why

Сейчас UI зашит в `agent/main.go` и продублирован в `agent/fixed/main.go`.
`agent/fixed` — отдельный Go-модуль с echo-заглушкой, поэтому он не может
переиспользовать Ollama-клиент, SQLite-доступ и tool-loop baseline-агента.
В результате preset 5 либо копирует код, либо остаётся нерабочим.

Кроме того, UI знает только один агент. Для сравнения baseline и fixed нужен
один интерфейс с выбором агента, но браузер не должен получать внутренние
адреса контейнеров и не должен зависеть от CORS.

## What changes

- Вынести общее ядро в `agent/internal/agentcore/`: конфиг, runtime settings,
  audit, Ollama-клиент, `db_query`, внешняя стена, HTTP-контракт агента.
- Оставить `agent/main.go` тонким entrypoint для preset 1–4.
- Переписать `agent/fixed/main.go` как реальный fixed-агент на общем ядре:
  Ollama → `db_query` → Ollama, strict prompt, input/output guards, read-only
  tool whitelist, immutable settings.
- Удалить вложенный `agent/fixed/go.mod`; fixed-бинарник собирается из того же
  Go-модуля.
- Вынести UI в отдельный Go-сервис `ui/` с реестром агентов и reverse-proxy:
  `GET /api/agents`, `GET /api/agents/{id}/status`,
  `POST /api/agents/{id}/settings`, `POST /api/agents/{id}/chat`.
- Добавить сервис `ui` в compose на `:8090`; убрать встроенный `/ui` из
  агентов.
- Обновить README, project.md и tasks: зафиксировать разницу
  baseline/fixed и статус реализации.

## Non-goals

- Не менять поведение пресетов 1–4 и контракт внешней стены.
- Не добавлять аутентификацию в UI: стенд локальный, UI не публикуется наружу.
- Не реализовывать attack harness и отчёты в этом бандле.
- Не делать fixed-агента настраиваемым: preset 5 по определению immutable.

## Risks

- Рефакторинг в общий пакет может сломать существующие unit-тесты; тесты
  переезжают вместе с кодом или адаптируются к экспортированным именам.
- UI-прокси создаёт дополнительный сетевой слой; ошибки агента нужно
  пробрасывать без потери статуса и тела.
- Strict-политика fixed может блокировать легитимные запросы; smoke должен
  включать разрешённый `SELECT count(*)`.
- Один Go-модуль для двух бинарников требует изменить Dockerfile/compose;
  проверить сборку обоих образов.

## Definition of done

- `agent` и `agent-fixed` собираются из одного модуля без дублирования UI.
- `agent-fixed` отвечает реальной моделью и выполняет только разрешённый
  `SELECT`; `DROP`/`DELETE` не исполняются.
- `POST /settings` fixed-агента возвращает `403`; `/status` показывает
  `preset=5`, `runtimeToggles=false`, `allowWrites=false`.
- `ui`-сервис отдаёт selector, проксирует status/settings/chat для обоих
  агентов и корректно показывает `403` fixed-агента.
- `go test ./agent/...`, `go vet ./agent/...`, `go test ./ui/...`,
  `go vet ./ui/...` зелёные.
- README и OpenSpec отражают фактическое состояние.
