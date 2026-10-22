# Proposal: add-builtin-guards (preset 2/4 prompt isolation)

## Why

`BUILTIN_GUARDS=on` выбирает `agent/prompts/guarded.txt`, но этот файл —
заглушка. Живой smoke P1→P2 это подтвердил: preset 2 вернул `200` и первый
абзац системного промпта. Пресет 2 в матрице заявлен как «только встроенная
защита», значит нужен реальный prompt isolation, а не только переключатель.

## What changes

- Наполнить `agent/prompts/guarded.txt` политикой:
  - пользовательский ввод и данные тула — недоверенные;
  - нельзя раскрывать системный промпт, правила, секреты и токены;
  - нельзя менять роль/политику по инструкции из данных;
  - `db_query` — только по контракту, без обхода read-only/write-policy;
  - ПДн минимизируются, чувствительные поля не выводятся целиком.
- Не добавлять служебные обёртки в user input: live-проверка показала, что
  слабая модель начинает выводить `<<TOOL_DATA>>` вместо ответа. Изоляция
  задаётся политикой в `guarded.txt`.
- В guarded follow-up после `db_query` явно помечать tool output как
  untrusted data; baseline-путь не меняется.
- Добавить unit-тесты на политику и guarded follow-up.
- Прогнать live P1→P2: prompt leak, injection, легитимный SELECT.
- Обновить `reports/red-team.md` и задачи `add-agent-core`/`add-defense-modes`.

## Non-goals

- Детерминированный классификатор prompt injection: это делает внешняя стена
  (`add-external-defense`).
- Гарантия «все атаки blocked»: prompt-only защита измеряется, а не
  декларируется.
- Preset 5: отдельный бандл `add-fixed-agent`.
- Изменение схемы БД, tool whitelist сверх текущего `db_query`.

## Risks

- Prompt injection нельзя закрыть промптом гарантированно; возможны bypass.
  Отчёт фиксирует измеренный результат, без «all blocked».
- Обёртка добавляет токены и меняет поведение модели. Влияние на latency
  измеряется smoke-прогоном.
- Санитизация маркеров должна применяться и к user input, и к tool data;
  иначе модель увидит поддельный закрывающий тег.
- Модель может игнорировать политику. Это ожидаемый риск preset 2; внешняя
  стена остаётся детерминированным слоем для preset 3/4.

## Definition of done

- `guarded.txt` содержит рабочую политику, а не placeholder.
- При `builtinGuards=true` user input и tool data обёрнуты; при `false`
  поведение baseline не меняется.
- Маркеры в недоверенном тексте обезврежены.
- Unit-тесты и `go vet` зелёные.
- Live P1/P2 и легитимный запрос записаны в отчёт; bypass не выдаётся за
  blocked.
