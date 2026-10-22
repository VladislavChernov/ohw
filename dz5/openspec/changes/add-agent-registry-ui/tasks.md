# Tasks: add-agent-registry-ui

## 1. Документация

- [x] Создать новый бандл `add-agent-registry-ui`.
- [x] Описать shared core, fixed-политику и UI-реестр.
- [x] Обновить README: таблица `agent` vs `agent-fixed`, ключевая разница
      preset 4 / preset 5, новые endpoints и quick start.
- [x] Обновить `openspec/project.md` и `add-fixed-agent`.

## 2. Общее ядро

- [x] Создать `agent/internal/agentcore/` с конфигом, settings, audit,
      Ollama, `db_query`, wall и HTTP-контрактом.
- [x] Перевести `agent/main.go` на общее ядро без изменения поведения 1–4.
- [x] Перенести/адаптировать unit-тесты ядра.
- [x] Удалить встроенный `/ui` и `uiTemplate` из baseline-агента.

## 3. Fixed agent

- [x] Удалить `agent/fixed/go.mod`; fixed собирается в модуле `ohw/dz5/agent`.
- [x] Реализовать реальный `runToolLoop` с Ollama и `db_query`.
- [x] Загружать `agent/fixed/prompts/fixed.txt`, fatal при пустом/отсутствующем.
- [x] Реализовать strict input guards.
- [x] Реализовать output guards для секретов/PII.
- [x] Реализовать read-only SQL whitelist: только `SELECT` по известным
      таблицам, без writes и `sqlite_master`.
- [x] Сделать settings immutable: `/status` с `preset=5`, `/settings` → `403`.
- [x] Удалить echo-заглушку и дублированный UI.
- [x] Unit-тесты guards, SQL policy, status/settings.
- [x] Интеграционный тест tool-loop на mock Ollama.

## 4. UI-сервис

- [x] Создать `ui/go.mod` и `ui/main.go` (stdlib only).
- [x] Реализовать `AGENT_REGISTRY` с валидацией.
- [x] Реализовать `/api/agents`.
- [x] Реализовать proxy на status/settings/chat с сохранением статусов.
- [x] Реализовать HTML selector и disabled-переключатели для fixed.
- [x] Добавить `ui/Dockerfile`.
- [x] Unit-тесты реестра и proxy (httptest).

## 5. Инфраструктура

- [x] Добавить `ui` в `infra/compose.yaml` на `:8090`.
- [x] Изменить `agent-fixed` build context на `../agent` + build arg/target.
- [x] Обновить `agent/Dockerfile` для сборки baseline и fixed.
- [x] Обновить quick start и endpoints в README.

## 6. Проверка

- [x] `go test ./agent/...` и `go vet ./agent/...`.
- [x] `go test ./ui/...` и `go vet ./ui/...`.
- [x] Собрать оба образа и UI через `docker buildx build --load` /
      `docker compose build`.
- [x] Поднять `ollama db agent agent-fixed wall ui`.
- [x] Baseline: UI проксирует status/chat/settings без регрессий.
- [x] Fixed: легитимный `SELECT count(*)` → 200; `DROP`/`DELETE` не исполнены.
- [x] Fixed: `POST /settings` → 403; UI показывает disabled.
- [x] Проверить, что `/healthz` не зависит от LLM/БД.
- [x] Compose default `ALLOW_RUNTIME_TOGGLES=on` для демо; UI-переключатели
      baseline активны.

## 7. Завершение

- [x] Обновить статусы в `project.md` и `add-fixed-agent`.
- [x] Все галочки только по факту проверки.
- [x] Коммит только `dz5/` и пуш после явной просьбы (запрошено).
