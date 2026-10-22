# Proposal: add-fixed-agent (preset 5)

## Why

Сейчас `agent/fixed/` — echo-заглушка: `/chat` возвращает `fixed agent echo:
<сообщение>`, не вызывает Ollama и не читает SQLite. При этом README и отчёты
описывают preset 5 как полноценную защищённую конфигурацию. Это разрыв между
записью и кодом.

Кроме того, UI-переключатели fixed agent только отображают env-состояние:
изменение чекбокса ничего не меняет. Для демонстрации preset 5 нужен реальный
tool-loop и работающие runtime-настройки.

## What changes

- Реальный `/chat` в `agent/fixed/`: Ollama → `db_query` → Ollama, как в
  основном агенте, с ответом `reply`, `preset=5`, `tool_calls`.
- Prompt isolation: `<<POLICY>>`, `<<USER>>`, `<<DATA>>` в
  `agent/fixed/prompts/fixed.txt`; данные БД не смешиваются с инструкциями.
- Входные guards: инъекции, roleplay, попытки раскрыть промпт.
- Выходные guards: секреты, PII, признаки утечки контекста.
- Tool whitelist: только `SELECT` по известным таблицам; записи — только при
  `ALLOW_WRITES=1` и подтверждении.
- Runtime-настройки fixed agent: `POST /settings` с `builtinGuards`,
  `externalGuard`, `allowWrites`; UI-переключатели реально меняют поведение.
- `/status` отражает фактическое состояние, а не только env.
- Тесты: unit на guards/tool policy, интеграционные на mock LLM/DB.

## Non-goals

- Внешний Guardrails Server для preset 5: по матрице у fixed agent внешняя
  защита `n/a`.
- Изменение пресетов 1–4 основного агента.
- Замена общего `db_query`/Ollama-клиента, если удастся переиспользовать код
  без дублирования.

## Risks

- Дублирование кода с `agent/`; при реализации вынести общие части в
  `agent/internal/` (LLM-клиент, SQLite, policy), не ломая сборки.
- Runtime-переключатели ослабляют защиту, если доступны снаружи. Для стенда
  включать `ALLOW_RUNTIME_TOGGLES=on` только в демо-профиле; в остальных —
  `off` и `403` на `/settings`.
- Реальный LLM делает ответы недетерминированными; тесты не должны ожидать
  конкретный текст, только инварианты (нет утечки, tool не выполнен).

## Definition of done

- `agent-fixed` отвечает реальной моделью, а не echo.
- Входная инъекция блокируется; легитимный запрос проходит.
- Попытка `DROP TABLE` не выполняется; `SELECT` работает.
- UI-переключатель меняет поведение без перезапуска.
- `go vet ./agent/fixed/...` и интеграционные тесты зелёные.
- README/red-team не заявляют больше, чем подтверждено.
