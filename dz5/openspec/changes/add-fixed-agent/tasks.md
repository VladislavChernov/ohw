# Tasks: add-fixed-agent (preset 5)

## 1. Планирование

- [x] Зафиксировать разрыв: `agent/fixed` был echo-заглушкой, UI не работал,
      сборка падала на `HandleStatus`/`HandleUI`.
- [x] Описать реальный tool-loop, guards, tool whitelist и immutable settings.
- [x] Вынести preset 5 из `add-agent-core` в отдельный бандл.
- [x] Вынести общий код в `agent/internal/agentcore/` (см.
      `add-agent-registry-ui`).

## 2. Код fixed agent

- [x] Переиспользовать Ollama-клиент, SQLite и HTTP-контракт из общего ядра.
- [x] Реализовать `runToolLoop`: LLM → `db_query` → LLM → `reply`.
- [x] Загружать `prompts/fixed.txt`; fatal при отсутствии/пустом файле.
- [x] Реализовать prompt isolation без literal placeholder-ов.
- [x] Входные guards: override, jailbreak, prompt leak, encoding, URL.
- [x] Выходные guards: секреты, PII, hidden context.
- [x] Tool whitelist: `SELECT` по известным таблицам; writes всегда запрещены.
- [x] Удалить echo-заглушку.
- [x] `GET /status` — фактическое состояние (`preset=5`, immutable).
- [x] `POST /settings` → `403` всегда.
- [x] UI вынесен в отдельный сервис `ui/`; в fixed встроенного UI нет.
- [x] Dockerfile: один Go-модуль, build arg `TARGET_PKG=./fixed`.

## 3. Тесты

- [x] Unit: input/output guards, tool policy, prompt loading.
- [x] Unit: immutable settings (`403`, состояние не меняется).
- [x] Integration на mock Ollama/DB: легитимный SELECT и `DROP`.
- [x] Проверить, что `/healthz` не зависит от LLM/БД.
- [x] `go test ./agent/...`, `go vet ./agent/...` зелёные.

## 4. Живая проверка

- [x] Поднять `ollama` + `db` + `agent-fixed`.
- [x] Легитимный `/chat` → 200 с реальным ответом и `tool_calls`.
- [x] `Ignore previous instructions...` → 403, LLM не вызвана.
- [x] `DROP TABLE users` → не выполнено; БД цела.
- [x] `POST /settings` → 403.
- [ ] PII-запрос → маска/блок (unit есть; live отдельно не зафиксирован).
- [x] UI selector и proxy chat/status проверены через `ui`-сервис.
- [ ] Зафиксировать результаты в `reports/<run-id>/` (это `add-reports`).

## 5. Завершение

- [x] Все проверки, кроме отчётов, зелёные.
- [x] Обновить README: preset 5 — реальный агент, не echo.
- [x] Коммит только `dz5/` после явной просьбы (запрошено).
