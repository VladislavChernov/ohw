# Задачи

## 1. Демо-контур (Streamlit :8503)

- [x] 1.1. `src/graphrag_proto/demo_ui/app.py`: sidebar с редактируемыми адресами
      (`INGESTION_URL`, `QUERY_URL`, `CONFIG_URL`), доменом и `X-API-Key` (дефолты из env;
      дефолт ключа `${GRAPH_AUTH_API_KEY:-changeme}`).
- [x] 1.2. Вкладка «Документы»: upload (txt/md; PDF в UI заблокирован с пояснением —
      JSON-контракт M1 не несёт бинарный контент), домен → показ успеха/ошибки;
      поллинг `GET /api/v1/ingestion/jobs/{job_id}` — стадии журнала до терминального;
      список последних задач; soft-delete по `source_url`+домен (с подтверждением).
- [x] 1.3. Вкладка «Запросы»: POST `/query` → `202 {task_id}`; блокирующее чтение SSE
      `GET /query/tasks/{task_id}/stream` с отрисовкой `status → token* → done` на лету
      (конверт ADR-016); итог: текст ответа, таблица `sources[{source_url, relevance}]`,
      `generation_time_s`; кнопка «Отмена» (`DELETE /query/tasks/{task_id}`),
      аккуратная обработка 404/409; история запросов (task_id + статус).
- [x] 1.4. HTTP-слой: все запросы с заголовком `X-API-Key`; таймауты; понятные ошибки
      (status code + text) вместо краша Streamlit.

## 2. Зависимости и compose

- [x] 2.1. `pyproject.toml`: добавить `streamlit>=1.40` и `requests>=2.32` в `dependencies`;
      обновить `uv.lock`.
- [x] 2.2. `pyproject.toml`: `[tool.pytest.ini_options].addopts = "-m \"not e2e\""`
      + `markers = ["e2e: сквозной тест против живого стека (ингestion+query)"]`.
- [x] 2.3. `infra/compose.yaml`: сервис `demo-ui` — профиль `llm`, порт `8503:8503`,
      build тот же образ (`ohw/demo-ui:prototype`, команда `graphrag-demo-ui`),
      env `INGESTION_URL=http://ingestion-api:8002`, `QUERY_URL=http://query-api:8000`,
      `CONFIG_URL=http://config-service:8001`, `X_API_KEY=${GRAPH_AUTH_API_KEY:-changeme}`,
      network `ohw_net`, depends_on config-service, ingestion-api, query-api.
- [x] 2.4. healthcheck `demo-ui`: `/_stcore/health` на `8503`.

## 3. E2e-харнесс

- [x] 3.1. `tests/test_demo_e2e.py` (marker `e2e`, авто-скип без адресов):
      CLI/env-адреса (`--e2e-ingest`, `--e2e-query`, `--e2e-key` / `E2E_*`);
      хелперы: `upload`, `wait_job`, `query_done` (SSE до `done`).
- [x] 3.2. Сценарий 1 «ingest → query → ответ со sources»: загрузить txt-документ,
      дождаться `succeeded`, `POST /query`, получить `done`, проверить `sources`
      непусты и содержат загруженный `source_url`.
- [x] 3.3. Сценарий 2 «soft-delete снимает источник»: delete документа → свежий query →
      `sources` без удалённого `source_url`.
- [x] 3.4. Таймауты/ретраи поллинга (пайп задаёт допуски), чистка временных файлов.
- [x] 3.5. `infra/scripts/run_demo_e2e.sh`: **сброс стека `docker compose down -v` по
      умолчанию** (`--keep-volumes` — без сброса; чистота volume'ов графа/вектора —
      предусловие детерминизма при top_k=5), подъём полного стека (`--profile config
      --profile graph --profile ingestion --profile llm`, `--wait`), прогон
      `pytest -m e2e`, вывод результата; `--keep-up` и `--down`. (Прогон pytest — через `uv`
      на хосте при наличии, иначе контейнер `ohw-python:3.13` с `host.docker.internal`.)

## 4. Документация

- [x] 4.1. `docs/demo_runbook.md`: поднять стек, открыть `:8503`, сценарии в браузере
      (загрузка → стадии → запрос → стрим → sources → отмена), прогон e2e (`bash
      infra/scripts/run_demo_e2e.sh`), ограничения (фейк-LLM/эмбеддинги до M3).
- [x] 4.2. Ссылка на runbook из `docs/README.md`.

## 5. Верификация

- [x] 5.1. `uv run pytest -q` (юнит/контрактные) — зелёно; e2e-модуль в прогоне скипается.
- [x] 5.2. `uv run ruff check .`, `uv run mypy` — чисто.
- [x] 5.3. Ручной браузерный проход по описанному runbook (или e2e-скрипт на живом стеке).
- [ ] 5.4. `/review` бандла и дельты; коммит + push `origin master`.