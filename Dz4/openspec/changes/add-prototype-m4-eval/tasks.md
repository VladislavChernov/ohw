# Задачи

## Eval-метрики (библиотека)

- [ ] `src/graphrag_proto/eval/metrics.py`:
      `retrieval_metrics(retrieved_urls, golden_urls) -> {recall_at5, precision_at5, mrr_at5, ndcg_at5}`;
      `generation_metrics(answer, golden_facts, judge) -> {groundedness, coverage, hallucination_rate}`;
      `lift_report(baseline, target) -> {delta: {...}, verdict}`.
- [ ] Тесты: `tests/test_eval_metrics.py` — recall/mrr/ndcg на синтетике; groundedness/coverage
      с фейк-judge; lift delta и verdict.

## Eval-раннер

- [ ] `infra/eval/run_eval.py` CLI:
      `--domain it|library|cinema`, `--mode baseline|hybrid`, `--corpus <path>`,
      `--dataset <path>`, `--out <dir>`; собирает pipeline (build_pipeline),
      инджестит корпус через Ingestion API (:8002), прогоняет датасет, пишет
      `lift_report.json` + `lift_report.md`.
- [ ] Baseline = graph_enabled False; hybrid = graph_enabled True (тумблер через
      `RETRIEVAL_GRAPH_ENABLED` или adapter_map).
- [ ] Ревизия: каждый прогон фиксирует `GET /api/v1/ingestion/revision?domain=`
      в run metadata (`done.revision`, run_id, date, mode, model).
- [ ] Режим генерации с judge: groundedness/coverage через `LLMInference`
      (FakeLLM в CI, реальный Qwen на целевой стек; env `EVAL_LLM_ADAPTER=fake|openai`).

## Датасеты

- [ ] `infra/eval/it/questions.jsonl` — 50 вопросов по корпусу docs/review/QA_review
      (ADR-015 формат; golden_sources = source_url загруженных документов;
      подтверждается реальностью retrieval, не «угадайки»).
- [ ] `infra/eval/library/questions.jsonl` — 10 вопросов (демо-корпус).
- [ ] `infra/eval/cinema/questions.jsonl` — 10 вопросов (демо-корпус).
- [ ] Валидация датасетов: тест `tests/test_eval_dataset.py` — парсится JSONL,
      `golden_sources` непустые, id уникальны, формат ADR-015.

## Контрактный тест LLM-адаптера

- [ ] `tests/test_llm_openai_adapter.py` — стаб-сервер (BaseHTTPRequestHandler):
      streaming SSE-дельты склеиваются в полный текст; non-streaming (single JSON);
      HTTP 500 → RuntimeError; таймаут → RuntimeError; заголовок `authorization`.
- [ ] Проверка factory: `LLM_ADAPTER=openai` + `LLM_BASE_URL` собирает адаптер
      против стаб-сервера.

## Документация и гейт

- [ ] `docs/prototype_requirements.md` M4: чеклист отмечен (кроме реального
      lift-прогона на целевом железе — помечается partial), статус вехи.
- [ ] `docs/operations_requirements.md` §5: выход eval-скрипта — артефакт выпуска.
- [ ] ADR-015 статус: подпункты метрик реализованы в раннере (частично, judge-шаг —
      на реальном LLM).
- [ ] `docs/05_adr_log.md` / `docs/history.md`: запись пуша.
- [ ] Финальный прогон ruff + mypy + pytest; коммит, push, CI green.