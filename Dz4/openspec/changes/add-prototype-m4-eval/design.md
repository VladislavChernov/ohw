# Design: M4 Eval-инфраструктура

## Корпус и датасет

**Корпус it-домена** — реальные документы платформы:
- `docs/*.md` (25 файлов: adr_log, design, api_reference, security, infrastructure и т.д.)
- `review/*.md` и `QA_review/*.md` — локальные файлы (не в git, .gitignore).

Загрузка в it-домен: Ingestion API, `source_url` = относительный путь `docs/05_adr_log.md`,
`doc_type=md`, тело — содержимое файла. Метадаты retrieval берутся из source_url.

**Датасет** — `{id, query, golden_sources: [source_url], golden_facts: [строка], category}`.
- `golden_sources` — реальные пути документов, из которых гарантированно извлекается ответ.
- `golden_facts` — 1-3 коротких фактических утверждения (для coverage).
- Категории (заимствованы из it-домена): `architecture`, `semantics`, `invariants`,
  `security`, `operations`, `retrieval`.

## Архитектура раннера

```
run_eval.py (CLI)
  ├── build pipeline (build_pipeline, inmemory/neo4j выбор)
  ├── ingest corpus (DocumentClient → Ingestion API :8002)
  ├── for mode in [baseline, hybrid]:
  │     toggle graph axis (RETRIEVAL_GRAPH_ENABLED / adapter_map)
  │     for each q in dataset:
  │         done = pipeline.run(q.query, domain, emit, revision=current_rev)
  │         met = retrieval_metrics(done.sources, q.golden_sources)
  │         if EVAL_LLM_ADAPTER != fake: gen = generation_metrics(...)
  │     aggregate → mode_report
  └── lift_report(baseline_report, hybrid_report) → lift_report.json/md
```

## Метрики (ADR-015)

- **Retrieval@5**: Recall@5, Precision@5, MRR@5, nDCG@5 (по golden_sources из `done.sources`).
- **Генерация**: groundedness (доля утверждений ответа, подтверждаемых контекстом),
  coverage (доля golden_facts, покрытых ответом), hallucination_rate.
  Judge = `LLMInference` (FakeLLM в CI → метрики-заглушки; реальный Qwen на целевом стеке).
- **Lift-report**: `{baseline: {...}, target: {...}, delta: {metric: target-baseline},
  verdict: "pass|fail"}` по «валютному» правилу ADR-015: groundedness/coverage ≥ baseline.

## Ревизия в eval-срезе

Каждый прогон фиксирует `revision = GET /api/v1/ingestion/revision?domain=it` +
`done.revision` из пайплайна → metadata: `{run_id, date, mode, model, revision}`.
Срез воспроизводим: фиксация rev позволяет повторный прогон на той же ревизии.

## Контрактный тест LLM-адаптера

Stub-сервер на `http.server.BaseHTTPRequestHandler`:
- `/v1/chat/completions` режим streaming: `data: {"choices":[{"delta":{"content":"а"}}]}\n...data: [DONE]`.
- non-streaming: `{"choices":[{"message":{"content":"..."}}]}`.
- Error: 500 / таймаут (sleep > timeout).
- Проверки: дельты склеиваются, хедер `Authorization`, ошибки → RuntimeError.

## Структура файлов

```
prototype/
├── src/graphrag_proto/eval/
│   └── metrics.py            # метрики + lift_report
├── infra/eval/
│   ├── run_eval.py           # CLI раннер
│   ├── it/questions.jsonl    # 50
│   ├── library/questions.jsonl  # 10
│   └── cinema/questions.jsonl   # 10
└── tests/
    ├── test_eval_metrics.py
    ├── test_eval_dataset.py
    └── test_llm_openai_adapter.py
```