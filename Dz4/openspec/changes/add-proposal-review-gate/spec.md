# Спека: Proposal Review Gate

## Scenario: proposal submitted

**WHEN** новое предложение появляется в `Proposals/`

**THEN** оно получает статус `draft`, владельца и evidence checklist, но не изменяет
`CONCEPT.md`, `docs/`, runtime или OpenSpec-контракт.

## Scenario: review record completed

**WHEN** аналитик заполняет review для proposal

**THEN** record содержит problem, current-state comparison, evidence, assumptions, risks,
accepted/rejected scope, decision, follow-up и условие повторного рассмотрения.

## Scenario: proposal accepted

**WHEN** review принимает решение `accepted`

**THEN** accepted scope переносится в новый OpenSpec change; proposal получает явную
ссылку на этот change, но не начинает реализацию автоматически.

## Scenario: proposal partially accepted

**WHEN** review принимает только часть предложения

**THEN** accepted slice получает отдельный follow-up change, rejected/deferred slices
перечисляются явно, а смешивание двух разных контрактов запрещается.

## Scenario: proposal deferred

**WHEN** evidence недостаточно для решения

**THEN** proposal получает статус `deferred`, измеримый missing gap и условие/срок
повторного review; оно не блокирует текущую систему.

## Scenario: proposal rejected

**WHEN** proposal не подходит под текущие цели, evidence или ограничения

**THEN** фиксируются причина, рассмотренные альтернативы и условия возможного возврата;
proposal не переносится в normative docs.

## Scenario: graph Data Map review

**WHEN** рассматривается `graph_as_data_map_manifest.md`

**THEN** review отдельно оценивает metadata-only projection, online graph expansion и
background mining, а также Turbopuffer только как возможный vector backend example.

## Scenario: scheduled analytical checkpoint

**WHEN** завершена текущая milestone или достигнут `M6-Growth / pre-connectors`

**THEN** запускается review queue, и для каждого proposal с владельцем фиксируется решение
или явная причина отсрочки.
