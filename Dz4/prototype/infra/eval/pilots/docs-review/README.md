# Пилотный eval-датасет: документация проекта и исторические ревью (10 вопросов)

Команда cline_Union_Alpha, 2026-09-17. Development-пилот качества ответов ассистента по
документации проекта: **не** финальный benchmark (ADR-015 требует минимум 50 вопросов) и
**не** приёмочный тест преимущества графа.

## Файлы

| Файл | Назначение |
|---|---|
| `questions.jsonl` | 10 вопросов: `query`, `golden_sources`, `golden_facts`, `reasoning_type`, `answerability`, `as_of`, `evidence_sections`, `evidence_policy`, `rubric` |
| `corpus-manifest.json` | Закрытый список 7 исходных документов, без рекурсивного обхода репозитория |
| `corpus_tools.py` | Offline `check`/`prepare` (snapshot + SHA-256) и явный `upload` через Ingestion API |
| `test_corpus_tools.py` | Автономные unittest: без Docker, HTTP и загрузки моделей |

`golden_sources` — канонические логические идентификаторы, совпадающие с `source_url`,
передаваемым Ingestion API (например `docs/invariants.md`), а не Windows-пути.

## Состав корпуса

Четыре источника публикуются в репозитории: `README.md`, `docs/invariants.md`,
`docs/03_retriever.md`, `docs/05_adr_log.md`.

Три источника существуют только локально и в Git не попадают по политике проекта
(`Dz4/review/` и `Dz4/prototype/reports/` исключены):

- `review/2026-09-14_team-senior-plus_review-05_semantic-cache.md`
- `review/2026-09-16_cline_review-08_docker-compose.md`
- `prototype/reports/run_eval_1789651539/lift_report.json` (run-артефакт; включается
  в snapshot как Markdown-обёртка полного JSON без правок и добавления ответов)

Вопросы 03/04/06/07 опираются на исторические ревью, 10 — на отчёт прогона. На машине
без этих файлов `prepare` завершится ошибкой отсутствия файла (fail-fast). Варианты:
выполнить `prepare` на машине с полным локальным корпусом и перенести snapshot, либо
подготовить укороченный вариант корпуса через `--manifest <json>` (исключив зависимые
вопросы из прогона). Публикация содержимого исторических ревью — отдельное решение.

**Не индексировать:** `questions.jsonl`, этот README, manifest, ревью с готовыми
ответами. Сами документы-источники индексировать допустимо: это open-book RAG.

## Куда попадают данные

Только через Ingestion API (не Cypher вручную): Neo4j — Source/Chunk/Entity и векторы;
SQLite ingestion (отдельный volume) — журнал джобов, DocumentRegistry, ревизия;
SQLite config — активный домен `it`. Профили и глоссарий монтируются из `domain_profiles` (конфигурация, не корпус). Valkey
не используется. Режим EXTRACT задаётся runtime-конфигурацией; для pilot upload используйте
явно выбранный профиль и проверяйте непустоту и содержательность графовых доказательств —
девять стадий не означают полноценного извлечения онтологии.

## Порядок запуска

1. Offline (Python 3.11+, только stdlib; не запускать с `-O` — проверки на assert):
   `python -B corpus_tools.py check` → `python -B test_corpus_tools.py` →
   `python -B corpus_tools.py prepare --root <корень репозитория ohw>`.
   Snapshot в `./snapshot`: копии документов + `manifest.lock.json` с SHA-256; перезапись запрещена.
2. Стек: [../../README-minimal.md](../../README-minimal.md) — build/up, проект `ohw-eval-docs-v1`.
3. Загрузка (последовательно, 7 документов, ожидание `succeeded` каждого):
   `docker compose -p <project> -f compose.eval-minimal.yaml run --rm --no-deps eval-runner python /proposal/corpus_tools.py upload --out /reports/docs-review-upload.json`
4. Проверить: 7 `succeeded` в receipt, ненулевую revision, Source/Chunk в Neo4j,
   отсутствие чужого корпуса; revision сверять до и после обеих веток.
5. Запросы: для этого пилота используйте `questions.jsonl`; полный graph-эксперимент с
   `questions_graph.jsonl` запускается отдельной командой из [../../README-minimal.md](../../README-minimal.md).
   Snapshot в штатный `--corpus` не передавать: `source_url` станут `document-NN.md` и разметка сломается.

## Ограничения интерпретации

- `both` разделяет vector-only `baseline` и vector→graph-expansion `target`; для сравнения
  используйте одинаковые revision/chunking/embeddings/K и `run_manifest.json`.
- `qa_log.jsonl` сохраняет seed chunk IDs, graph paths/depth/boost, provenance и компонентные
  метрики; `trace.jsonl` появляется только с `--trace`.
- Groundedness/coverage считаются через LLM-judge; в `--no-judge` и `--retrieval-only`
  они не вычисляются.
- 10 вопросов — development-выборка, не статзначимая; положительные числа не доказывают
  пользу GraphRAG.
## Проверено автором

Offline-тесты: 4 OK (WSL и одноразовый `python:3.11-slim` без сети); `check` OK —
10 вопросов, 7 источников. `docker compose … config --quiet` = 0. Реальный ingestion,
модели и eval-прогоны не выполнялись; snapshot реального корпуса создаётся оператором.
