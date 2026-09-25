# Review-record: ingest_refactoring_solution

```text
review-record:
  proposal: docs/archive/ingest_proposals_v1/ingest_refactoring_solution.txt
  reviewer/date: sol_pro, 2026-09-23 (решение), ревью-регистрация 2026-09-26
  decision: partial
  accepted_scope: >
    Двойные метки узлов (Requirement/Concept/Contract) + плоские синонимы, единый
    _identity_key с NFKC/trim/casefold, рёбра Chunk-[:MENTIONS]-> Entity с
    привязкой по chunk_id вместо текстового свойства source_ids.
  rejected_scope: >
    Полная переработка ExtractStage/CommitStage._write в объёме исходного текста —
    принята по результатам, а не как буквальная инструкция.
  evidence: >
    openspec/changes/eval-graph-contribution-experiment/tasks.md §8 (DoD 8.1-8.4);
    docs/data_model.md §6; Dz4/analitic/eval/judge_calibration_analysis.md §4.1
    (graph axis даёт 0 выигрыша при неразрешённых связях).
  assumptions: >
    Идея «Облако тегов + Столицы» выбрана как минимальное изменение, а не как
    оптимальная архитектура графа.
  risks: >
    DoD 8.4 (unit-тесты + live-прогон) не закрыт: приёмка по пп. 8.1-8.4 ещё не
    подтверждена, acceptance остаётся условным.
  follow_up_change: >
    Закрыть DoD 8.4 в eval-graph-contribution-experiment; L2-06 (no-op повторной
    загрузки) должен подтвердить, что дубли не плодятся.
  revisit_when: >
    После закрытия DoD 8.4 — пересмотреть статус на accepted или rejected по
    фактическому выигрышу graph axis.
```

**Суть.** Из пяти дошедших до архива предложений это единственное обработанное: его требования
перенесены в DoD главы 8 и реализованы в пунктах 8.1–8.3. Пункт 8.4 открыт, поэтому решение
зафиксировано как `partial`, а не `accepted`.

Материал остаётся в архиве: он остаётся нормативным обоснованием DoD 8.1–8.3, пока задача 8.4
не закрыта.
