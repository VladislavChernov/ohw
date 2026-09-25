# Review-record: turbopuffer dual-write

```text
review-record:
  proposal: >
    Изначально docs/archive/ingest_proposals_v1/how_it_use_in_turbopuffer.md.
    Материал перенесён в learning/hybrid_rag_dual_write_example.md как учебный
    пример; каталог learning/ не публикуется, поэтому в репозитории его нет.
  reviewer/date: sol_pro, 2026-09-23 (вердикт), ревью-регистрация 2026-09-26
  decision: deferred
  accepted_scope: >
    Канон разделения текста и графа уже принят независимо от turbopuffer:
    текст живёт в payload векторной оси, projection хранит только IDs, tags и
    связи (docs/01, learning/hybrid_rag_overview_learning.md §4).
  rejected_scope: >
    Перенос векторного payload во внешний управляемый сервис (turbopuffer) как
    способ снизить RAM/SSD-стоимость.
  evidence: >
    learning/hybrid_rag_overview_learning.md §4 — экономия за 10x, но платный API;
    BLG-A02 в backlog.md — профиль RAM/VRAM полного корпуса ещё не измерен,
    поэтому выигрыш не оценён в числах.
  assumptions: >
    Дешёвый локальный векторный движок покрывает потребность прототипа; это
    допущение перестаёт работать при росте корпуса.
  risks: >
    Внешний сервис в контуре — это отдельный провайдер, цена, SLA и вопрос
    соответствия требованиям к изоляции доменов.
  follow_up_change: >
    Нет. Принятой части не требуется: канон разделения зафиксирован в docs/,
    внешний сервис не принимается.
  revisit_when: >
    Возобновить, когда измеренный профиль RAM/VRAM (BLG-A02) покажет, что
    локальный движок перестаёт укладываться в бюджет стенда.
```

**Суть.** Идея «граф без текста + векторный payload во внешнем хранилище» methodologically
верна, но её ключевой аргумент — экономия памяти — в прототипе не проверен: профиль полного
корпуса ещё не измерен. Поэтому `deferred`, а не `rejected`: решение может вернуться.

Материал перенесён в `learning/` как учебный пример dual-write и продолжает быть полезным
вне зависимости от вердикта. В git он не лежит: полный текст доступен по коммиту `f0e3f91`
(путь `Dz4/Ingest/proposals/how_it_use_in_turbopuffer.md`).
