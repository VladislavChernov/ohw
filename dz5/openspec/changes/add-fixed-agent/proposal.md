# Proposal: add-fixed-agent (preset 5)

## Why

`agent/fixed/` был echo-заглушкой: `/chat` возвращал `fixed agent echo:
<сообщение>`, не вызывал Ollama и не читал SQLite. При этом README и отчёты
описывали preset 5 как полноценную защищённую конфигурацию. Это разрыв между
записью и кодом.

Кроме того, fixed-агент был отдельным Go-модулем с дублированным UI и не мог
переиспользовать Ollama-клиент, SQLite и tool-loop baseline-агента.

## What changes

- Реальный `/chat` в `agent/fixed/`: Ollama → `db_query` → Ollama, как в
  основном агенте, с ответом `reply`, `agent=fixed`, `preset=5`, `tool_calls`.
- Prompt isolation: `agent/fixed/prompts/fixed.txt` без literal placeholder-ов;
  данные БД передаются как untrusted data.
- Входные guards: инъекции, roleplay, попытки раскрыть промпт, encoding, URL.
- Выходные guards: секреты, PII, признаки утечки контекста.
- Tool whitelist: только `SELECT` по известным таблицам; writes запрещены
  всегда; `SELECT *`, `UNION`, stacked statements и `sqlite_master` запрещены.
- Immutable settings: `/status` показывает `preset=5`, `/settings` → `403`.
- Общее ядро `agent/internal/agentcore/`; `agent/fixed/go.mod` удалён.
- UI вынесен в отдельный сервис `ui/` (см. `add-agent-registry-ui`).
- Тесты: unit на guards/tool policy, интеграционные на mock LLM/DB.

## Non-goals

- Внешний Guardrails Server для preset 5: по матрице у fixed agent внешняя
  защита `n/a`.
- Изменение пресетов 1–4 основного агента.
- Настраиваемый preset 5: fixed по определению immutable.

## Risks

- Дублирование кода с `agent/`; решено общим пакетом
  `agent/internal/agentcore/`.
- Runtime-переключатели ослабляют защиту baseline, если доступны снаружи.
  В compose для демо `ALLOW_RUNTIME_TOGGLES=on`; fixed всё равно immutable.
- Реальный LLM делает ответы недетерминированными; тесты проверяют инварианты
  (нет исполнения опасного SQL, нет PII), а не конкретный текст.

## Definition of done

- `agent-fixed` отвечает реальной моделью, а не echo.
- Входная инъекция блокируется; легитимный запрос проходит.
- Попытка `DROP TABLE` не выполняется; `SELECT count(*)` работает.
- `POST /settings` → `403`; состояние не меняется.
- `go test ./agent/...`, `go vet ./agent/...` зелёные.
- README/red-team не заявляют больше, чем подтверждено.
