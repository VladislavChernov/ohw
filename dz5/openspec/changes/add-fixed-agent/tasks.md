# Tasks: add-fixed-agent (preset 5)

## 1. Планирование

- [x] Зафиксировать разрыв: `agent/fixed` — echo-заглушка, UI не работает,
      сборка падает: `srv.HandleStatus`/`srv.HandleUI` не определены
      (`main.go:358-359`).
- [x] Описать реальный tool-loop, guards, tool whitelist и runtime settings.
- [x] Вынести preset 5 из `add-agent-core` в отдельный бандл.

## 2. Код fixed agent

- [ ] Вынести/переиспользовать Ollama-клиент и SQLite-доступ (общий пакет или
      аккуратное копирование с тестами).
- [ ] Реализовать `runToolLoop`: LLM → `db_query` → LLM → `reply`.
- [ ] Загружать `prompts/fixed.txt`; fatal при отсутствии/пустом файле.
- [ ] Реализовать prompt isolation `<<POLICY>>` / `<<USER>>` / `<<DATA>>`.
- [ ] Входные guards: override, jailbreak, prompt leak, encoding, URL.
- [ ] Выходные guards: секреты, PII, hidden context.
- [ ] Tool whitelist: `SELECT` по известным таблицам; writes только с
      `ALLOW_WRITES=1` и подтверждением.
- [ ] Удалить echo-заглушку.
- [ ] `GET /status` — фактическое состояние.
- [ ] `POST /settings` + `ALLOW_RUNTIME_TOGGLES`, mutex, валидация.
- [ ] UI: чекбоксы вызывают `/settings`, показывают preset и ошибки.
- [ ] Логи: preset, guards, blocked, reason, tool calls, latency; без секретов.
- [ ] Dockerfile: Go build → scratch/distroless, промпт внутри образа.

## 3. Тесты

- [ ] Unit: guards (positive/negative), tool policy, preset/status.
- [ ] Unit: `/settings` — 403 при выключенных toggles, 400 на неверные поля,
      `confirmWrites` для записей.
- [ ] Integration на mock LLM/DB: легитимный запрос, инъекция, `DROP`, PII.
- [ ] Проверить, что `/healthz` не зависит от LLM/БД.
- [ ] `go vet ./agent/fixed/...` зелёный.

## 4. Живая проверка

- [ ] Поднять `ollama` + `agent-fixed` (и `db-seed`).
- [ ] Легитимный `/chat` → 200 с реальным ответом.
- [ ] `Ignore previous instructions...` → 403, LLM не вызвана.
- [ ] `DROP TABLE users` → не выполнено; БД цела.
- [ ] PII-запрос → маска/блок.
- [ ] Переключить чекбокс в UI → поведение изменилось без рестарта.
- [ ] Зафиксировать результаты в `reports/<run-id>/`.

## 5. Завершение

- [ ] Все проверки зелёные; галочки только по факту.
- [ ] Обновить README: preset 5 — реальный агент, не echo.
- [ ] Коммит только `dz5/` после явной просьбы.
