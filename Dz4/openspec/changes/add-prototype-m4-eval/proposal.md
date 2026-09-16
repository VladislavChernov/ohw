# Proposal: M4 Eval-инфраструктура — валидация thesis прототипа

## Почему

Прототип написан, но thesis не доказан: измеримого подтверждения, что гибридный подход
(граф + вектор) лучше vector-only, нет. Веха M4 критична, т.к. без eval-гейта
(ADR-015) прототип остаётся «работающим кодом без валидации утверждений из §1».

**Что именно отсутствует:**
- `questions.jsonl` (ADR-015: `{id, query, golden_sources, golden_facts, category}`,
  мин. 50 вопросов/домен для базового среза)
- eval-раннер (прогон baseline vs hybrid, Retrieval@K=5, groundedness/coverage,
  lift-отчёт в JSON+md)
- контрактный тест `OpenAICompatibleAdapter` против реального OpenAI-совместимого
  endpoint (стаб-сервер, streaming/non-streaming/errors)
- интеграция ревизии знаний в eval-срез (done.revision фиксируется в каждом прогона)

## Что делаем

- **Корпус it-домена**: `docs/*.md` (25 файлов) + `review/*.md` + `QA_review/*.md`
  (локально, не в git). Corpus загружается в it-домен через Ingestion API
  (source_url = относительный путь к файлу, doc_type=md).
- **Eval-датасет it**: 50 вопросов в `prototype/infra/eval/it/questions.jsonl`
  (ADR-015 формат: `{id, query, golden_sources: [source_url], golden_facts: [строка], category}`,
  golden_sources ссылаются на source_url загруженных документов).
- **Eval-датасеты library/cinema**: по 10 синтетических вопросов на основе
  domain-профилей (авторы, жанры, фильмы) с demo-корпусом.
- **Eval-раннер** (`prototype/infra/eval/run_eval.py` + библиотека
  `prototype/src/graphrag_proto/eval/metrics.py`):
  - Два режима: baseline (graph off, vector-only) и target (hybrid, graph+vector).
  - Метрики ретрива (K=5): Recall@5, Precision@5, MRR@5, nDCG@5 по golden_sources.
  - Метрики генерации (LLM judge): groundedness, coverage, hallucination_rate
    (при FakeLLM — fixture, при реальном LLM — реальный judge).
  - Выход: lift-report в JSON + markdown (delta по каждой метрике).
  - Ревизия фиксируется в каждом прогона (run metadata).
- **Контрактный тест** `OpenAICompatibleAdapter`:
  `tests/test_llm_openai_adapter.py` — stub-сервер на BaseHTTPHandler,
  проверки: streaming-дельты, non-streaming, HTTP 500, timeout.

## Спека

См. `specs/m4-eval-infrastructure/spec.md`.

## Проверка

1. `uv run pytest tests/test_eval_metrics.py tests/test_llm_openai_adapter.py -q` — CI.
2. `uv run python infra/eval/run_eval.py --domain it --mode baseline --corpus ../../docs` —
   прогон baseline (inmemory, детерминированный эмбеддер).
3. `uv run python infra/eval/run_eval.py --domain it --mode hybrid --corpus ../../docs` —
   прогон hybrid.
4. Выходной `lift_report.json` и `lift_report.md` — вывод метрик.
5. На целевом стеке (compose + GPU): `run_eval.py --mode baseline|hybrid` с
   реальным LLM и bge-m3.
