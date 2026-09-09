# Дизайн: add-demo-ui-e2e (Demo UI :8503 + e2e-прогон)

> Тонкий клиент над существующими контрактами M1/M2. Ядро (ingestion, query, адаптеры)
> НЕ трогаем. Новый код — `demo_ui` (Streamlit) + `tests/test_demo_e2e.py` (opt-in).

## D1. Контур демо

```
Browser ── :8503 (demo-ui, Streamlit)
            │
            ├── INGESTION_URL http://ingestion-api:8002   (upload, jobs, soft-delete)
            ├── QUERY_URL     http://query-api:8000       (POST /query, SSE stream, DELETE cancel)
            └── CONFIG_URL    http://config-service:8001  (GET /config/domain/profiles → выбор домена)
            все запросы: X-API-Key (env X_API_KEY, дефолт `${GRAPH_AUTH_API_KEY:-changeme}`)
```

- Streamlit запускается console-скриптом `graphrag-demo-ui` внутри контейнера, браузер
  ходит на host-порт `8503` — адреса сервисов внутри UI берутся по внутренним именам
  compose (`ingestion-api`, `query-api`, `config-service`), поэтому UI не зависит от маппинга
  host-портов.
- Состояние демо — сессионный стейт Streamlit (список задач/запросов), переживает `st.rerun`.

## D2. Экраны

- **Sidebar:** адреса трёх сервисов (редактируемые), домен (выбор из
  `GET /api/v1/config/domain/profiles`), `X-API-Key` (по умолчанию из env, не логируется).
- **Tab «Документы»:**
  1. `st.file_uploader` (первостепенно `txt`, `md`; `doc_type` из расширения, PDF в UI
     отключён с пояснением) → `source_url` = `s://demo/<basename выбранного файла>`;
     `POST /api/v1/ingestion/documents` — **JSON-тело** `{source_url, domain, doc_type,
     content}`, где `content` — **сырой текст** (НЕ base64, НЕ multipart: сервер M1 пишет
     `content.encode()` как есть, `ingestion_service/app.py:184-186`). PDF через этот
     контракт не передаётся — бинарные загрузки требуют отдельной правки ядра (вне бандла);
     PDF-пайплайн покрыт юнит-тестами `tests/test_ingestion_pdf.py`;
  2. поллинг `GET /api/v1/ingestion/jobs/{job_id}` на таймере (1 с) до терминального,
     отрисовка последовательности стадий журнала (та же семантика, что в
     `ingestion_service`); статус `succeeded`/`failed`/`cancelled`;
  3. таблица последних задач сессии (job_id, source_url, статус);
  4. кнопка **soft-delete** для `domain`+`source_url` (safe-подтверждение),
     `DELETE /api/v1/ingestion/documents?domain=&source_url=`, 404 — сообщение.
- **Tab «Запросы»:**
  1. text_input вопроса → `POST /query` c JSON-телом `{"query": ..., "metadata":
     {"domain": ...}}` (контракт `query_service/app.py:43`) → `202 {task_id}`;
  2. блокирующее чтение `GET /query/tasks/{task_id}/stream` через `requests` (stream=True):
     SSE-события разбираются по `event:`/`data:` (конверт ADR-016), статусы и токены
     выводятся в live-блок (`st.status` / `st.markdown` после завершения);
  3. по `done`: текст ответа, `st.dataframe` `sources[{source_url, relevance}]`,
     `generation_time_s`;
  4. «Отмена» → `DELETE /query/tasks/{task_id}` (404/409 → snackbar);
  5. история запросов сессии: task_id × статус.

## D3. E2e-харнесс

- `tests/test_demo_e2e.py`, маркер `e2e`; `pyproject.toml`:
  `addopts = "-m \"not e2e\""`, `markers = ["e2e: ..."]` — стандартный прогон их не видит.
- Адреса: pytest CLI-флаги (`--e2e-ingest`, `--e2e-query`, `--e2e-key`) или env
  `E2E_INGEST`/`E2E_QUERY`/`E2E_KEY`;
  нет адресов → `pytest.skip`. HTTP-клиент — `requests` (новая зависимость в
  `pyproject.toml`, нужен и UI, и харнессу).
- Поллинг задач/стримов — с общими таймаутами (INGEST ≤ 60 с, SSE до `done` ≤ 120 с),
  ретраи на 503/408.
- Сценарии ровно по spec (upload→succeeded→query→sources содержит url; после soft-delete —
  не содержит). Используется временный файл `tmp_path`, имя `source_url`
  (`s://demo-e2e-<uuid>.txt`) уникально — не мешает повторным прогонам.
- Ходят только по host-портам (`localhost:8002`, `localhost:8000`) — как настоящий браузер.

## D4. Compose / развёртывание

- `demo-ui`: `build ../` (тот же Dockerfile), `image ohw/demo-ui:prototype`,
  `command: ["uv","run","streamlit","run","src/graphrag_proto/demo_ui/app.py",
  "--server.address=0.0.0.0","--server.port=8503","--server.headless=true"]`,
  `profiles: ["llm"]`, `ports: ["8503:8503"]`,
  env из D1, `depends_on: [config-service, ingestion-api, query-api]`,
  healthcheck: `/_stcore/health` (интервалы как у остальных).
- Прогон стека: `docker compose --profile config --profile graph --profile ingestion
  --profile llm up -d --wait` (llm тянет valkey/query-api/query-worker/demo-ui).
- `infra/scripts/run_demo_e2e.sh`: **сброс стека** `docker compose down -v` (чистые
  volume'ы графа/вектора/валекса — обязательное предусловие e2e, т.к. top_k=5 на
  хэш-эмбеддингах не гарантирует попадание документа в sources при чужом мусоре;
  флаг `--keep-volumes` отключает сброс) → подъём (`--profile config --profile graph
  --profile ingestion --profile llm`, `--wait`) → `uv run pytest -m e2e
  --e2e-ingest=http://localhost:8002 --e2e-query=http://localhost:8000
  --e2e-key=${GRAPH_AUTH_API_KEY:-changeme}` → вывод; `--keep-up`/`--down` (по умолчанию
  не роняет стек после прогона).

## D5. Почему Streamlit

- Уже используется как целевой стек фазы 2 (конфигуратор `:8501`, Topology UI `:8502`,
  ADR-019) — единый UI-стек без нового фреймворка.
- Тонкий (ничего не знает о внутренностях нитей/ротации), тестируется тем же e2e-слоем.
- Для demo не нужен WS: SSE читается блокирующим HTTP-стримом из воркера Streamlit.

## Модули

| Модуль | Изменение |
|---|---|
| `src/graphrag_proto/demo_ui/__init__.py`, `app.py` | новый (Streamlit) |
| `infra/compose.yaml` | сервис `demo-ui` |
| `pyproject.toml`, `uv.lock` | `streamlit>=1.40`, pytest addopts/markers |
| `tests/test_demo_e2e.py` | новый, marker `e2e` |
| `infra/scripts/run_demo_e2e.sh` | новый |
| `docs/demo_runbook.md`, `docs/README.md` | документация |