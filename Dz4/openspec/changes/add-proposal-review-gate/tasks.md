# Задачи: Proposal Review Gate

> Это аналитическая и процессная change. Код и runtime не изменяются до отдельного
> follow-up change для принятой части предложения.

- [ ] 1.1. Зафиксировать владельца, триггер и периодичность Proposal Review Gate.
- [ ] 1.2. Добавить шаблон review-record и evidence checklist для `Proposals/`.
- [ ] 1.3. Определить статусы `draft`, `in-review`, `accepted`, `partial`, `deferred`,
      `rejected`, `superseded` и правила переходов.
- [ ] 2.1. Провести инвентаризацию существующих документов в `Proposals/` и назначить
      владельцев/следующие evidence gaps.
- [x] 2.1a. **Инвентаризация архива `Ingest/proposals/` закрыта 2026-09-26.** Пять
      предложений поколения 2026-09-19/21 зарегистрированы в `review-records/`: одно
      `partial` (`ingest_refactoring_solution`), одно `deferred` с переносом материала
      в личные методички (`turbopuffer_dual_write`), три `deferred`
      (`multidomain_graph`, `prompt_and_entity_resolution`). Источник статуса —
      review-record, не README архива.
- [x] 2.1b. **Проставить вердикты по трём `deferred`:** измеримые gap и условия
      повторного review записаны в самих review-record (`revisit_when`).
- [ ] 2.2. Провести первый review `graph_as_data_map_manifest.md` по трём слоям:
      metadata-only, online expansion experiment, background mining.
- [ ] 2.3. Записать evidence matrix и решение по каждому слою proposal.
- [ ] 3.1. Для `accepted`/`partial` создать follow-up OpenSpec change с ограниченным scope.
- [ ] 3.2. Для `deferred` указать измеримый gap и условие повторного review.
- [ ] 3.3. Для `rejected` сохранить причину и альтернативу; не менять нормативные docs.
- [ ] 4.1. Добавить review gate в milestone/operations checklist перед `M6-Growth /
      pre-connectors`.
- [ ] 4.2. Провести walkthrough на одном принятом и одном отклонённом/отложенном proposal.
- [ ] 5.1. Проверить, что proposal остаётся ненормативным до review decision и follow-up change.
