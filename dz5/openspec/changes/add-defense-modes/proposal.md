# Proposal: add-defense-modes (runtime-переключение пресетов 1–4)

## Why

Сейчас `BUILTIN_GUARDS`, `EXTERNAL_GUARD` и `ALLOW_WRITES` читаются только при
старте контейнера. UI-чекбоксы на `/ui` декоративные: они показывают env, но
не меняют поведение. Для демонстрации «атака → включили защиту → повторили»
нужно переключение на лету без перезапуска и без правки `.env`.

## What changes

- Runtime-конфигурация в Go под `sync.RWMutex`; `/chat` берёт согласованный
  снимок настроек на запрос.
- `POST /settings` с `{builtinGuards, externalGuard, allowWrites}`:
  валидация, применение, пересчёт пресета 1–4.
- `allowWrites=true` требует явного `confirmWrites=true`; иначе `400`.
- `ALLOW_RUNTIME_TOGGLES=on` включает endpoint; иначе `403`. В compose для
  демо — `on`; при прямом запуске агента default — `off`.
- Динамический выбор промпта: `minimal.txt` / `guarded.txt` загружаются при
  старте, выбор — по текущему `builtinGuards`.
- Внешняя защита: при `externalGuard=true` вход/выход идут через Guardrails
  Server; при недоступной стене — fail-closed `503`.
- UI вызывает `/settings` через отдельный `ui`-сервис, показывает текущий
  preset и ошибки; чекбоксы становятся рабочими.
- Audit-log JSONL: время, старое/новое состояние, preset, источник, результат.
- L3: compose-лимиты памяти/CPU, таймауты, restart-policy; без
  context-cancellation чужого кода.

## Non-goals

- Preset 5 и UI-реестр: бандлы `add-fixed-agent` и `add-agent-registry-ui`.
- Сохранение runtime-настроек между рестартами (пока in-memory; env — дефолт).
- Аутентификация UI/API: стенд локальный; для внешнего доступа нужен reverse
  proxy с auth (вне ДЗ).

## Risks

- Открытый `/settings` позволяет выключить защиту. Для демо это нужно; в
  не-демо профиле `ALLOW_RUNTIME_TOGGLES=off`.
- Гонки: чтение конфига без lock приведёт к несогласованному пресету. Все
  чтения — под `RLock`, запись — под `Lock`; снимок на запрос.
- Включение `allowWrites` может привести к разрушению БД. Требовать
  подтверждение, журналировать, документировать `db-restore`.
- Внешняя стена может быть недоступна в момент переключения; не делать
  «тихий» fallback на прямой Ollama.

## Definition of done

- `POST /settings` меняет preset 1–4 без рестарта.
- `/status` и UI-сервис отражают фактическое состояние.
- При `externalGuard=true` и остановленной стене `/chat` → `503`.
- `allowWrites` без подтверждения → `400`; с подтверждением — работает.
- Audit-log содержит переходы.
- Тесты на конкурентность и fail-closed зелёные.
