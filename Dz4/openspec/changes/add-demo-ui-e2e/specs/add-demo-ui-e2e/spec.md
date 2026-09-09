# Delta Spec: add-demo-ui-e2e (Demo UI :8503 + e2e-прогон)

## ADDED Requirements

### Requirement: Демо-контур (Streamlit :8503)

Демо-клиент ДОЛЖЕН открываться в браузере на `:8503` и позволять выполнить сквозной сценарий
без CLI: загрузить документ (txt/md, выбор домена; PDF из UI исключён — JSON-контракт M1
`/ingestion/documents` не несёт бинарный контент), наблюдать стадии INGEST до
терминального статуса, отправить вопрос в Query API и получить стриминговый ответ
(конверт ADR-016) с текстом, источниками и таймингами. Все запросы ДОЛЖНЫ проходить
с заголовком `X-API-Key`. Адреса сервисов и ключ ДОЛЖНЫ задаваться env
(`INGESTION_URL`, `QUERY_URL`, `CONFIG_URL`, `X_API_KEY`) и быть редактируемыми в sidebar.

#### Scenario: Загрузка документа и наблюдение за стадиями

- **WHEN** пользователь загружает txt/md-файл во вкладке «Документы» для активного домена
  (PDF из UI исключён: JSON-контракт `/ingestion/documents` не поддерживает бинарный контент)
- **THEN** UI отправляет `POST /api/v1/ingestion/documents` (JSON `{source_url, domain,
  doc_type, content}`, `content` — сырой текст) и показывает стадии журнала
  (`GET /api/v1/ingestion/jobs/{job_id}`) до `succeeded`/`failed`

#### Scenario: Запрос со стримингом и источниками

- **WHEN** пользователь отправляет вопрос во вкладке «Запросы»
- **THEN** UI получает `202 {task_id}`, читает `GET /query/tasks/{task_id}/stream`,
  отображает `status → token* → done` в реальном времени и по `done` показывает текст ответа,
  таблицу `sources[{source_url, relevance}]` и `generation_time_s`

#### Scenario: Отмена запроса

- **WHEN** пользователь нажимает «Отмена» для незавершённой задачи
- **THEN** UI вызывает `DELETE /query/tasks/{task_id}`; терминальная задача — `409`,
  которая показывается понятным сообщением

#### Scenario: Soft-delete документа

- **WHEN** пользователь удаляет документ (вкладка «Документы»)
- **THEN** UI вызывает `DELETE /api/v1/ingestion/documents` с `domain`+`source_url` и
  показывает результат (404 для отсутствующего/уже удалённого)

### Requirement: E2e-харнесс (opt-in pytest marker `e2e`)

Набор сценариев ДОЛЖЕН прогонять полный цикл на живом стеке через host-интерфейсы:
upload → успешный INGEST → query → `done` со `sources`, содержащими загруженный
`source_url` → soft-delete → новый query без этого `source_url`. Адреса — через
CLI-флаги (`--e2e-ingest`, `--e2e-query`, `--e2e-key`) или env `E2E_*`; при отсутствии
адресов тесты скипаются. Предусловие детерминизма: стек должен быть **чистым**
(`docker compose down -v` перед подъёмом — script/обёртка выполняют сброс; при
top_k=5 и хэш-эмбеддингах наличие чужих чанков может вытеснить загруженный документ из
`done.sources`). В стандартный прогон `uv run pytest -q` харнесс НЕ входит
(исключение по marker в `addopts`).

#### Scenario: ingest → query → ответ со sources

- **WHEN** временный txt-документ загружен и INGEST перешёл в `succeeded`
- **THEN** `POST /query` даёт `task_id`, SSE-стрим завершается `done`, `sources` непусты
  и содержат `source_url` загруженного документа

#### Scenario: soft-delete снимает источник с поиска

- **WHEN** документ мягко удалён
- **THEN** свежий query даёт `done`, `sources` которого не содержат удалённый `source_url`

### Requirement: Compose-интеграция demo UI

Сервис `demo-ui` ДОЛЖЕН разворачиваться в профиле `llm` (тот же признак, что и Query-стек),
слушать `8503`, healthcheck'иться по `/_stcore/health`, ходить к сервисам по внутренней
сети (`http://ingestion-api:8002`, `http://query-api:8000`, `http://config-service:8001`)
и зависеть от них (`depends_on`). Зависимость `streamlit>=1.40` ДОЛЖНА добавляться
в `pyproject.toml` с обновлением `uv.lock`.