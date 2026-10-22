# Tasks: add-defense-modes (runtime presets 1–4)

## 1. Планирование

- [x] Зафиксировать проблему: env-only конфиг, декоративные чекбоксы.
- [x] Описать runtime settings, `POST /settings`, fail-closed и audit.
- [x] Определить границу с `add-fixed-agent` (preset 5).

## 2. Конфигурация

- [x] `RuntimeSettings` + `sync.RWMutex` в `agent/settings.go`.
- [x] Загрузить оба промпта при старте; выбор по `builtinGuards`
      (`loadPrompts`, `Config.promptFor`).
- [x] `presetOf(settings)` для 1–4.
- [x] Env defaults + `ALLOW_RUNTIME_TOGGLES` (default `off`).
- [x] `AUDIT_LOG` (default `/data/audit.jsonl`; в compose `/audit/audit.jsonl`).

## 3. API

- [x] `GET /status` — фактическое состояние, `runtimeToggles`.
- [x] `POST /settings` — строгая валидация JSON, атомарное применение.
- [x] `403` при выключенных toggles; `400` на неверные поля/типы.
- [x] `allowWrites=true` требует `confirmWrites=true`.
- [x] Audit JSONL на успех и отказ; без секретов.
- [x] `/chat` берёт снимок настроек; внешний guard → `503` при ошибке стены.

## 4. UI

- [x] Чекбоксы вызывают `/settings`.
- [x] Показ preset и состояния guards.
- [x] Confirm для `allowWrites`.
- [x] Обработка `400/403/503`, перечитывание `/status`.
- [x] Убрать дублирующиеся скрытые checkbox-элементы.

## 5. Compose / L3

- [x] `ALLOW_RUNTIME_TOGGLES` в compose, default `off`; для демо — `on`.
- [x] `GUARDRAILS_BASE_URL`, `GUARDRAILS_TIMEOUT` в env агента.
- [x] Memory limits и restart-policy (wall 2048M; общий AnalyzerEngine, без
      per-call утечки ~300 MiB).
- [x] `AUDIT_LOG` volume `/audit`, права записи.

## 6. Тесты

- [x] Unit: preset 1–4, `/settings` 200/400/403.
- [x] Unit: `allowWrites` + `confirmWrites`.
- [x] Concurrent: чтение `/status` + запись `/settings` без data race
      (`go test -race ./...`).
- [x] Integration (live): external → wall остановлена → `/chat` `503`.
- [x] Integration (live): переключение без рестарта меняет маршрут
      (preset 3 → инъекция `403`; preset 1 → `/chat` 200).
- [x] UI smoke (HTTP): `/ui` отдаёт JS с `/settings`, смена настроек обновляет
      `/status`; браузерный клик не автоматизирован.

## 7. Живая проверка

- [x] Preset 1 → атака → включить builtin → повтор → сравнить.
      P1: prompt leak/PII утекли; P2: отказ на leak/PII/injection, легитимный
      SELECT работает (`add-builtin-guards`).
- [x] Preset 3/4 → легитимный запрос проходит через wall.
- [x] Остановить wall → `503`, не прямой Ollama.
- [x] `allowWrites` без confirm → `400`.
- [ ] `allowWrites` с confirm → запись и `db-restore`.
      Политика проверена unit-тестом `TestQueryDBWriteGate`; live-модель не
      выдала исполняемый SQL, БД восстановлена через `db-restore`.
- [ ] Записать результаты в `reports/<run-id>/`.

## 8. Завершение

- [ ] Все проверки зелёные; галочки по факту.
- [x] README: runtime switching, ограничение `ALLOW_RUNTIME_TOGGLES`.
- [ ] Коммит только `dz5/` после явной просьбы.
