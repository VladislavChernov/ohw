# Proposal: Demo UI + ручной e2e-прогон (браузер → загрузка документа → ответ по нему)

> Продолжение ДЗ4 после M2 (`add-prototype-m2-query`, `a9c36f3`).
> Строится на M2: асинхронный Query API (`:8000`), Ingestion API (`:8002`),
> Config Service (`:8001`), реальный COMMIT в Neo4j — всё есть на уровне REST/SSE.
> Данный бандл добавляет тонкий **демо-клиент** (Streamlit `:8503`) и **ручной/автоматический
> e2e-прогон** по полному стеку: «зайти в браузер → поднять контур → загрузить документ →
> сделать запрос → увидеть ответ + источники».
> Нового бизнес-логики в ядро НЕ вносит: UI — клиент уже существующих контрактов.

## Зачем

После M2 проверка сквозного сценария — это curl + подглядывание в Neo4j Browser/Grafana.
Нет точки входа, где «как человек» можно: загрузить файл, наблюдать стадии обработки,
задать вопрос и увидеть стриминговый ответ с источниками. Нет и быстрого способа прогонять
«примерные» e2e по живому стеку (up → ingest → query → проверить sources → down) без
ручного копирования curl-команд. Нужен минимальный **демо-контур** и **e2e-харнесс**.

## BR (бизнес-требования)

- **BR-1. Демо UI (`:8503`, Streamlit).** Браузерная точка входа: загрузка документа
  (txt/md; PDF — вне демо: JSON-контракт M1 `/ingestion/documents` не несёт бинарный
  контент, его поддержка — отдельная правка ядра) c выбором домена → живой статус стадий
  INGEST; запрос → стриминг `status/token/*/done` (конверт ADR-016) с текстом ответа,
  таблицей `sources` (`source_url`, `relevance`) и `generation_time_s`; soft-delete
  документа. Все вызовы — с `X-API-Key`.
- **BR-2. Прозрачная конфигурация.** Адреса сервисов и API-ключ приходят из env
  (`INGESTION_URL`, `QUERY_URL`, `CONFIG_URL`, `X_API_KEY`) со значениями по умолчанию для
  compose-стека; в UI адреса видны и переопределяются (полезно для локального запуска).
- **BR-3. E2e-харнесс (opt-in).** Набор тестов `test_demo_e2e.py` (pytest, marker `e2e`,
  по умолчанию выключен): сквозной цикл на живом стеке — upload → `succeeded` → query →
  `done` со `sources` → soft-delete → источник исчезает из `sources`. Адреса задаются
  env/флагами. Обёртка `infra/scripts/run_demo_e2e.sh` сбрасывает стек (`down -v` —
  предусловие детерминированности при top_k=5) и гоняет их.
- **BR-4. Автономность от юнит-тестов.** Бандл не ломает `uv run pytest -q` (e2e-тесты
  исключены по marker), `ruff`, `mypy` остаются зелёными.

## Что делаем

- **`prototype/src/graphrag_proto/demo_ui/app.py`** — Streamlit-приложение:
  - sidebar: базовые адреса (редактируемые), домен, X-API-Key;
  - вкладка **«Документы»**: upload (txt/md — PDF в UI ограничен JSON-контрактом M1) → `POST /api/v1/ingestion/documents` →
    поллинг `GET /api/v1/ingestion/jobs/{job_id}` (стадии журнала) до терминального;
    список последних задач; кнопка **soft-delete** по `source_url`/домену;
  - вкладка **«Запросы»**: `POST /query` → `202 {task_id}` → стрим
    `GET /query/tasks/{task_id}/stream` (SSE, конверт ADR-016): статусы, токены,
    итоговый `done`; вывод текста ответа, таблицы `sources` и таймингов; кнопка **отмена**
    (`DELETE /query/tasks/{task_id}`, обработка 409); история `task_id` со статусом.
- **`infra/compose.yaml`** — сервис `demo-ui` (профиль `llm`, порт `8503:8503`,
  команда `graphrag-demo-ui` — console script обёртки `streamlit run`),
  healthcheck `/_stcore/health`,
  env-адреса сервисов по внутренней сети `ohw_net`, `depends_on` config/ingestion/query.
- **`pyproject.toml`**:
  - зависимость `streamlit>=1.40`;
  - `[tool.pytest.ini_options]`: `addopts = "-m \"not e2e\""` (marker `e2e` — отдельно).
- **`tests/test_demo_e2e.py`** — e2e-харнесс (`@pytest.mark.e2e`):
  - фикстура читает адреса: `--e2e-ingest`, `--e2e-query`, `--e2e-key` (CLI) / `E2E_*` env;
  - сценарий: upload временного txt → дождаться `succeeded` → `POST /query` →
    дождаться `done` (SSE) → `sources` непуст и содержит загруженный `source_url` →
    soft-delete → повторный query → `sources` больше не содержит этот `source_url`;
  - не требует доступа к внутренним сервисам (ходят по host-портам).
- **`infra/scripts/run_demo_e2e.sh`** — `docker compose --profile config --profile graph
  --profile ingestion --profile llm up -d --wait`, прогон `pytest -m e2e`, опциональный down.
- **`docs/demo_runbook.md`** — как поднять, как погонять в браузере, как прогнать e2e.

## Спека

- `specs/add-demo-ui-e2e/spec.md` — «Delta Spec»: требования к демо-контуру (`:8503`),
  e2e-харнессу, compose и зависимостям (по BR-1..BR-4).

## Найденные при верификации дефекты ядра M1/M2 (исправлены этим бандлом)

- `task_queue.py:214` — `xreadgroup(group=..., consumer=...)` несовместим с redis-py 8.x
  (kwargs переименованы в `groupname`/`consumername`): воркер молча крутился в цикле
  с ~99% CPU, задачи не уходили из очереди. Модульные тесты мокали очередь/redis —
  вскрыл только живой e2e. Исправлено позиционным вызовом с новыми именами.
- `retrieval/adapters/neo4j.py:234` — `EXISTS(c.embedding)`: Neo4j 5.26 возвращает
  SyntaxError (property-existence-синтаксис удалён из Cypher) → каждый query падал в
  `pipeline_error` (видно в `error`,   а не `done`). Заменено на `c.embedding IS NOT NULL`.
- `infra/compose.yaml` — у `ingestion-api` не было `command:` (унаследовал бы
  `graphrag-config` вместо нужного `graphrag-ingestion`): стек фэйлил бы ingest.
  Добавлен явный `command: ["graphrag-ingestion"]`.

## Проверка

1. `uv run pytest -q` — зелёно (e2e по умолчанию не гоняются);
   `uv run ruff check .`, `uv run mypy` — чисто.
2. `infra/scripts/run_demo_e2e.sh` на живом стеке: upload → `succeeded` → query → `done`
   со `sources` → soft-delete → `sources` без источника.
3. В браузере `http://localhost:8503`: загрузка файла, стадии, запрос со стримингом,
   отмена запроса (409 на повторную отмену).