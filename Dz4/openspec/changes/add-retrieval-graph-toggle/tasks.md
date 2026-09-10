# Задачи

## 1. Тумблер графовой оси в Pipeline

- [x] 1.1. `src/graphrag_proto/retrieval/pipeline.py`: чтение `graph_search_enabled`
      из `profile["retrieval"]` (дефолт `true`), env `RETRIEVAL_GRAPH_ENABLED=true|false`
      перекрывает профиль.
- [x] 1.2. При `false`: graph-фьючер не создаётся, `skeleton_rows = []`, эмитится
      `status: {"stage": "graph", "enabled": false}`; векторная ось не меняется.
- [x] 1.3. `src/graphrag_proto/retrieval/retrievers.py`: убрать мёртвый комментарий про
      флаг; поведение `GraphRetriever` не трогаем.
- [x] 1.4. `domain_profiles/domain_profile.{it,library,cinema}.yaml`: в секцию `retrieval`
      добавить `graph_search_enabled: true`.

## 2. Тайминги ретрива

- [x] 2.1. В `done` (ADR-016) добавить `retrieval_time_s` (граф ∥ вектор + rerank +
      Context Assembly) и `total_time_s` (весь `run()`); `generation_time_s` сохранить
      (аддитивно, обратно совместимо).
- [x] 2.2. `src/graphrag_proto/demo_ui/app.py`: отображать новые тайминги в итоге
      запроса рядом с `generation_time_s`.

## 3. Тесты

- [x] 3.1. `tests/test_retrieval_pipeline.py`: `graph_search_enabled: false` в профиле →
      graph store не вызывается, `enabled: false` в статусе; по умолчанию — вызывается.
- [x] 3.2. env-override: `RETRIEVAL_GRAPH_ENABLED=false` перекрывает `true` в профиле.
- [x] 3.3. `done` содержит `retrieval_time_s`/`total_time_s` (числа >= 0), поля
      совместимы с существующим парсером UI (`_event_body`).

## 4. Документация и приёмка

- [x] 4.1. `docs/demo_runbook.md` + `docs/demo_user_guide.md`: раздел «Скорость граф
      вкл/выкл» — два прогона e2e с `RETRIEVAL_GRAPH_ENABLED` и сравнение
      `retrieval_time_s`.
- [x] 4.2. `uv run pytest -q` (зелёно), `uv run ruff check .`, `uv run mypy` — чисто.
- [x] 4.3. Живой A/B на стеке: два прогона `infra/scripts/run_demo_e2e.sh`
      (`RETRIEVAL_GRAPH_ENABLED=true` и `false`), в `done` два набора таймингов
      (граф вкл > граф выкл по `retrieval_time_s` или объяснение).
- [x] 4.4. `/review` бандла и дельты (APPROVE, фикс edge-case env); коммит + push `origin master`.
