# Runbook демо-контура

Браузерный прогон сквозного сценария «загрузить документ → запрос → ответ со
`sources`». Демо не требует знания REST-контрактов — всё за UI (Streamlit `:8503`).

## Подъём стека

```bash
docker compose --profile config --profile graph --profile ingestion --profile llm up -d --wait
```

После этого доступны:

| Сервис | URL |
|--------|-----|
| Demo UI (Streamlit) | http://localhost:8503 |
| Ingestion API | http://localhost:8002 |
| Query API | http://localhost:8000 |
| Config Service | http://localhost:8001 |

Ключ API по умолчанию — `changeme` (или `GRAPH_AUTH_API_KEY` при старте стека).
В sidebar UI адреса и ключ редактируются.

## Сценарии в браузере

### 1. Загрузка документа

1. Вкладка **«Документы»** → выберите **домен**.
2. Выберите файл **txt/md**. PDF в демо недоступен: JSON-контракт Ingestion API
   (`POST /api/v1/ingestion/documents`) не несёт бинарный контент; PDF-пайплайн
   покрывается юнит-тестами (`tests/test_ingestion_pdf.py`).
3. **Загрузить** → UI поллит `GET /api/v1/ingestion/jobs/{job_id}` и показывает
   стадии журнала до терминального статуса (`succeeded`/`failed`).

### 2. Запрос

1. Вкладка **«Запросы»** → текст вопроса → **Отправить запрос**.
2. `POST /query` возвращает `task_id`; UI блокирующе читает SSE-стрим
   `GET /query/tasks/{task_id}/stream` и отрисовывает `status → token* → done`
   (конверт ADR-016).
3. Итог: текст ответа, таблица `sources` (`source_url`, `relevance`),
   `generation_time_s`.
4. **Отменить задачу** — `DELETE /query/tasks/{task_id}` (404/409 обрабатываются
   аккуратно).

### 3. Soft-delete

Во вкладке **«Документы»** → выбрать источник из списка → **Удалить (soft-delete)**.
Источник снимается с поиска: следующий запрос не вернёт его в `sources`.

## Прогон e2e-харнесса

Харнесс (`tests/test_demo_e2e.py`, marker `e2e`) гоняет весь цикл на живом стеке:

```bash
bash infra/scripts/run_demo_e2e.sh
```

По умолчанию скрипт **сбрасывает volume'ы** (`docker compose down -v`) — это
предусловие детерминизма: `top_k=5` и хэш-эмбеддинги не гарантируют попадание
загруженного документа в `sources` при чужом мусоре в графе. После прогона стек
остаётся поднятым (UI доступен для ручных сценариев). Сценарий сам выбирает запуск
`pytest -m e2e`: через `uv` на хосте, а если его нет — через контейнер
`ohw-python:3.13` (адреса внутри подменяются на `host.docker.internal`).

Флаги: `--keep-volumes` (не сбрасывать), `--down` (погасить стек после прогона).

В обычный `uv run pytest -q` харнесс не входит (`addopts = "-m 'not e2e'"`).

## A/B: скорость граф вкл/выкл (graph_search_enabled)

Тумблер графовой оси: `RETRIEVAL_GRAPH_ENABLED=true` (по умолчанию) / `false`.
Env перекрывает значение из `retrieval.graph_search_enabled` в Domain Profile
(`domain_profiles/domain_profile.*.yaml`). При `false` графовой запрос к Neo4j
не выполняется — retrieval идёт только по векторам.

Сравнение скорости (два прогона):

```bash
# 1. граф вкл (по умолчанию)
docker compose down -v && docker compose --profile config --profile graph --profile ingestion --profile llm up -d --wait
RETRIEVAL_GRAPH_ENABLED=true  bash infra/scripts/run_demo_e2e.sh --keep-volumes 2>&1 | grep "retrieval_time_s"

# 2. граф выкл
RETRIEVAL_GRAPH_ENABLED=false bash infra/scripts/run_demo_e2e.sh --keep-volumes 2>&1 | grep "retrieval_time_s"
```

Сравните `retrieval_time_s` из `done`-payload (отображается UI и в логах e2e).
Время `generation_time_s` не зависит от графа; `total_time_s` = retrieval + генерация.

## Ограничения демо (до M3)

- **Эмбеддинги и LLM — детерминированные заглушки** (`deterministic`, FakeLLM):
  «ответ» — шаблонный текст; осмысленный ответ по содержимому документа появится на
  фазе M3 (реальные EMBED/EXT + bge-reranker). Контекст в запрос попадает настоящий:
  уже сейчас можно подключить реальный llama.cpp через `LLM_BASE_URL`.
- **Retrieval верхнеуровневый (top-k)**: не используется реранкер/графовые паттерны —
  это фаза M3.