# Proposal: Граф-тумблер (`graph_search_enabled`) + тайминги ретрива для A/B

> Продолжение ДЗ4 после `add-demo-ui-e2e` (`e7180d0`). Строится на M2-контуре:
> QueryPipeline выполняет две независимые оси поиска — графовую (`GraphRetriever`) и
> векторную (`VectorRetriever`) параллельно (`pipeline.py:90-94`).
> Флаг `graph_search_enabled` уже объявлен в SSOT-конфиге (`infra/config/namespaces.yaml`,
> namespace `retrieval` и `flags`) и описан в доках (CONCEPT, docs/04 §5.1, docs/06),
> **но не читается кодом** — графовая ось исполняется всегда (`retrievers.py:52` — комментарий).

## Зачем

1. Нет способа сравнить поведение и **скорость запросов** «с графовой осью» vs «без неё»:
   единственный путь сейчас — костыли (например, заставить `GraphStoreProvider.query`
   поднять `NotImplementedError` или развернуть пустой граф). Нужен штатный тумблер.
2. Нет таймингов ретрива: `done` несёт только `generation_time_s` (время генерации LLM,
   `pipeline.py:112`). Время граф/вектор не видно ни в UI, ни в e2e — нельзя мерить
   разницу двух режимов на живом стеке.
3. Подготовка к M3: флаг и метрика понадобятся при реальных EMBED/EXT + reranker —
   сумма работы маленькая, а «долг» к M2 закрывает.

## BR (бизнес-требования)

- **BR-1. Тумблер графовой оси.** `QueryPipeline.run()` читает
  `graph_search_enabled` из секции `retrieval` активного Domain Profile.
  При `false` графовая ось **не исполняется** (запрос к graph store не выполняется,
  скелет пуст), контекст собирается только из векторного блока. Флаг присутствует
  в профилях доменов (`domain_profiles/*.yaml`, `retrieval.graph_search_enabled: true`).
- **BR-2. Env-override для A/B.** `RETRIEVAL_GRAPH_ENABLED=true|false` (env) перекрывает
  значение из профиля — переключение без правки YAML и пересборки, для двух прогонов
  на живом стеке.
- **BR-3. Тайминги ретрива.** `done` (конверт ADR-016) дополняется полями
  `retrieval_time_s` (граф ∥ вектор + rerank + Context Assembly) и `total_time_s`
  (весь pipeline); `generation_time_s` сохраняется. Поля аддитивны, обратная
  совместимость контракта сохраняется.
- **BR-4. Наблюдаемость в демо.** Вкладка «Запросы» показывает новые тайминги рядом
  с `generation_time_s`.
- **BR-5. Автономность.** Бандл не ломает `uv run pytest -q`, `ruff`, `mypy`;
  контракты `GraphStoreProvider`/`VectorStoreProvider` и SSE-конверт не меняются.

## Что делаем

- **`src/graphrag_proto/retrieval/pipeline.py`** — чтение `graph_search_enabled`
  (профиль → env-override `RETRIEVAL_GRAPH_ENABLED`); при выключенной оси:
  graph-фьючер не создаётся, `skeleton_rows = []`, статус `graph` эмитится с
  `{"enabled": false}`; замер времени осей/сборки контекста; в `done` добавляются
  `retrieval_time_s`, `total_time_s`.
- **`src/graphrag_proto/retrieval/retrievers.py`** — убираем мертвый комментарий про
  флаг (флаг читается в pipeline); поведение `GraphRetriever` не меняется.
- **`domain_profiles/domain_profile.{it,library,cinema}.yaml`** — в секцию `retrieval`
  добавляется `graph_search_enabled: true`.
- **`src/graphrag_proto/demo_ui/app.py`** — отображение `retrieval_time_s`/
  `total_time_s` в итоге запроса (рядом с `generation_time_s`).
- **`tests/test_retrieval_pipeline.py`** — новые юнит-тесты: выкл → graph store не
  вызывается; вкл → вызывается; done содержит новые тайминги; env-override
  перекрывает профиль.
- **`docs/demo_runbook.md` / `docs/demo_user_guide.md`** — раздел «Сравнение
  скорость граф вкл/выкл»: два прогона `run_demo_e2e.sh` с `RETRIEVAL_GRAPH_ENABLED`
  и сравнение `retrieval_time_s` (приёмка по BR-3).

## Спека

- `specs/add-retrieval-graph-toggle/spec.md` — «Delta Spec»: требования и сценарии
  по BR-1..BR-5. Контракт ADR-016 не изменяется (аддитивные поля).

## Проверка

1. `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` — чисто.
2. Юнит-тесты toggle: выкл — `graph_store.query` не вызывается; env-override.
3. Живой A/B: `RETRIEVAL_GRAPH_ENABLED=true` vs `false` — два прогона
   `infra/scripts/run_demo_e2e.sh` (или ручной query), сравнение `retrieval_time_s` из
   `done`; при `false` статус `graph` в стриме имеет `{"enabled": false}`.
4. UI `:8503`: в ответе видны `retrieval_time_s`/`total_time_s`.