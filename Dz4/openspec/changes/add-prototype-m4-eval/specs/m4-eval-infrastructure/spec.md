# Change Specification: M4 Eval-инфраструктура

**Slug:** `m4-eval-infrastructure`
**Статус:** proposed
**Цель:** закрыть веху M4 ADR-015 — измеримая валидация гибридного подхода
(граф+вектор vs vector-only) и контрактный тест LLM-адаптера.

## Поведение

### 1. Eval-датасеты (артефакты данных)

- `infra/eval/{domain}/questions.jsonl` — JSON Lines, запись:
  `{"id", "query", "golden_sources": [source_url], "golden_facts": [строка], "category"}`.
- `it`: 50 записей (корпус `docs/*.md` + `review/*.md` + `QA_review/*.md`);
  `library`/`cinema`: 10 записей каждая (демо-корпус).
- `id` уникален в пределах домена; `golden_sources` непустой; `query` непустой.
- Datasets валидируются тестом `tests/test_eval_dataset.py` (парсятся, непустые
  golden_sources, уникальные id).

### 2. Метрики ретрива (K=5, ADR-015)

`eval/metrics.retrieval_metrics(retrieved_urls: list[str], golden_urls: set[str]) -> dict`:
- `recall_at5` = |relev & retrieved@5| / |golden|
- `precision_at5` = |relev & retrieved@5| / 5
- `mrr_at5` = 1/rank первого релевантного (0 если нет)
- `ndcg_at5` = DCG@5 / IDCG@5 (бинарная релевантность по golden)

### 3. Метрики генерации

`eval/metrics.generation_metrics(answer: str, golden_facts: list[str], judge: LLMInference)`:
- `groundedness` — доля утверждений ответа, подтверждаемых контекстом (judge-LLM);
- `coverage` — доля golden_facts, покрытых ответом (judge-LLM);
- `hallucination_rate` — доля утверждений ответа без источника.
- При `judge=FakeLLM` (CI) метрики помечаются `"mode": "fake"` и не влияют на verdict.

### 4. Lift-отчёт

`eval/metrics.lift_report(baseline: dict, target: dict) -> dict`:
- `{baseline, target, delta: {metric: target-baseline}, verdict: "pass"|"fail"}`.
- Verdict: `pass`, если target.groundedness ≥ baseline.groundedness и
  target.coverage ≥ baseline.coverage (валютное правило ADR-015).

### 5. Eval-раннер CLI

`infra/eval/run_eval.py --domain <it|library|cinema> --mode <baseline|hybrid>
--corpus <dir> --dataset <path> --out <dir>`:
- Собирает pipeline через `build_pipeline`; baseline = graph axis выключен
  (`RETRIEVAL_GRAPH_ENABLED=false`), hybrid = включён.
- Инджестит корпус через Ingestion API (:8002), source_url = относительный путь файла.
- Для каждого вопроса выполняет `pipeline.run(q.query, domain, emit, revision)`.
- Фиксирует в metadata прогона: `{run_id, created_at, mode, model, revision}`.
- Пишет `lift_report.json` и `lift_report.md` в `--out`.

### 6. Контракты LLM-адаптера (OpenAICompatibleAdapter)

- `POST {base_url}/v1/chat/completions`, тело `{model, messages, temperature, max_tokens, stream}`,
  заголовок `Authorization: Bearer <api_key>`.
- streaming: SSE-дельты `data: {"choices":[{"delta":{"content":"..."}}]}` склеиваются
  в полный текст; терминатор `data: [DONE]`.
- non-streaming: `{"choices":[{"message":{"content":"..."}}]}`.
- HTTP 500 / сетевой сбой / таймаут → `RuntimeError` (не глотается).
- Проверяется `tests/test_llm_openai_adapter.py` против стаб-сервера.

## Влияние на другие спецификации

- `docs/prototype_requirements.md` M4: чеклист закрывается (кроме реального
  lift-прогона на целевом железе — partial).
- `docs/invariants.md` L5-04: eval-гейт реализован на уровне раннера.
- ADR-015: метрики реализованы.

## Тест-план

- `tests/test_eval_metrics.py` — recall/precision/mrr/ndcg и groundedness/coverage
  на синтетике (фейк-judge).
- `tests/test_eval_dataset.py` — валидация датасетов.
- `tests/test_llm_openai_adapter.py` — streaming/non-streaming/errors/timeout.
- CI: `uv run pytest tests/test_eval_metrics.py tests/test_eval_dataset.py tests/test_llm_openai_adapter.py`.