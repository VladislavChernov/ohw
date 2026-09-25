# Архив предложений по ингесту (v1)

> Перенесено из `Ingest/proposals/` (2026-09-23) — гигиена репозитория, исключение
> дрейфа контекста при следующих прогонах.
>
> **Источник статуса — не этот README, а review-record** в бандле
> `openspec/changes/add-proposal-review-gate/review-records/`. Решения зарегистрированы
> там 2026-09-26; таблица ниже — указатель, а не источник.

## Состав

| Файл | Решение | Review-record |
|---|---|---|
| `ingest_refactoring_solution.txt` | `partial` | [`ingest_refactoring_solution.md`](../openspec/changes/add-proposal-review-gate/review-records/ingest_refactoring_solution.md) |
| `multidomain_graph_structure.md`, `multidomain_graph_ingest.md` | `deferred` | [`multidomain_graph.md`](../openspec/changes/add-proposal-review-gate/review-records/multidomain_graph.md) |
| `prompt_and_entity_resolution.md` | `deferred` | [`prompt_and_entity_resolution.md`](../openspec/changes/add-proposal-review-gate/review-records/prompt_and_entity_resolution.md) |

`ingest_refactoring_solution.txt` — «Облако тегов + Столицы»: двойные метки, единый
`_identity_key`, ребро `MENTIONS` с привязкой по `chunk_id`. Требования перенесены в DoD
8.1–8.4 бандла `eval-graph-contribution-experiment`; 8.1–8.3 закрыты, **8.4 (тесты + live)
открыт**, поэтому решение `partial`, а не `accepted`. Материал остаётся нормативным
обоснованием DoD до закрытия 8.4.

Три остальных проработки — мультидоменная топология, мультидоменный пайплайн и
промпты с Entity Resolution. Все три `deferred`: первые две подняты в мультидоменность,
которая не наступила (предпосылки — `add-source-connectors`, 0 из 10 задач), а семантический
ER конфликтует с уже принятым инвариантом L3-06. Подробности и условия возврата в очередь —
в review-record.

## Что ушло из архива

`how_it_use_in_turbopuffer.md` (dual-write: граф без текста + векторный payload во внешнем
хранилище) перенесён в в личные методички как учебный пример и
больше не является проектным предложением. Review-record:
[`turbopuffer_dual_write.md`](../openspec/changes/add-proposal-review-gate/review-records/turbopuffer_dual_write.md).

## Аналитические разборы

По оставшимся материалам — в `Ingest/analitic/`: `analysis_2026-09-21_sol_pro.md`,
`analysis_architectures_vs_current_GLM.md`, `analysis_ingest_storage_industry_GLM.md`.
Аналитика по оценке качества судьи вынесена отдельно, в `analitic/eval/`.

## История

Файлы доступны в истории коммитом `f0e3f91` (2026-09-22) по путям `Dz4/Ingest/proposals/`.
