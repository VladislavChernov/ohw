# Документация: API Reference (Query / Config / Ingestion / Glossary)

> **Версия:** v6 (итерация поверх базы v5)
> **Последнее обновление:** 2026-09-05
>
> Отношение к другим документам: потоки — `docs/03_retriever.md` (цикл генерации),
> `docs/02_pipeline_and_normalizer.md` (этапы ingestion); эндпоинты Config — `docs/04_services_config.md`;
> контракты адаптеров — `docs/adapters_specification.md`; сеть и порты — `docs/04_services_config.md` §1.

## 1. Синхронный и асинхронный контуры

- **Синхронный контур (база v5):** Config / Ingestion / Glossary обслуживают операцию до ответа
  (`200 OK` с результатом). Используется для административных и служебных операций.
- **Асинхронный контур (v6):** Query API принимает задачу мгновенно (`202 Accepted` + `task_id`),
  обработка выполняется пулом Query Workers через Task Queue (Valkey / Redis Streams),
  доставка результата — по WebSockets / SSE. Модель зафиксирована в `docs/00_hi_level_architecture.md`
  и `docs/history.md` (Этап 6).

Общий формат данных — JSON (`application/json`). Аутентификация — HTTP-заголовок `X-API-Key`
(значение берётся из `namespace: auth.api_key`, по умолчанию `changeme`).

## 2. Стандартизированные коды ошибок

| Код  | Описание |
|------|----------|
| `400 Bad Request` | Невалидный JSON/тело, нарушение синтаксиса YAML-профиля, дублирование `unique_key` |
| `401 Unauthorized` | Сервис требует `X-API-Key`, ключ не передан или неверен |
| `422 Unprocessable Entity` | Логическая ошибка онтологии: `edge_types` ссылаются на несуществующие типы узлов |
| `500 Internal Server Error` | Сбой СУБД или инфраструктуры очередей при коммите транзакции |

---

## 3. Query API (:8000) — асинхронный контур v6

Цикл генерации — 7 шагов по `docs/03_retriever.md`: эмбеддинг запроса → Graph Retriever →
Vector Retriever → Reranker → Context Assembly → LLM Generation → стриминг ответа.
Сервис обслуживает WebSockets-сессии в сетевом контуре (язык реализации — на усмотрение
владельца; прототип — ADR-020, процедура замены — `docs/web_layer_replacement.md`);
см. `docs/00` §1.

### 3.1. Приём задачи: `POST /query`

```
POST /query
Content-Type: application/json
X-API-Key: changeme

{
  "query": "Приведёт ли использование shell=True к уязвимости RCE?",
  "metadata": { "domain": "it" }
}
```

Ответ `202 Accepted` — задача принята, обработка отложена:

```json
{
  "task_id": "q_4f2a9c1e",
  "status": "queued",
  "accepted_at": "2026-09-05T17:20:00Z"
}
```

### 3.2. Статус задачи: `GET /query/tasks/{task_id}`

Опрос статуса без стриминга:

```json
{
  "task_id": "q_4f2a9c1e",
  "status": "running",
  "stage": "llm"
}
```

Жизненный цикл статуса: `queued → running → succeeded | failed | cancelled`.

### 3.3. Стриминг результата: WebSockets / SSE

Канал доставки — WebSockets (постоянное соединение, обслуживание сотен сессий) либо SSE (HTTP-стрим).
События:

- `status` — смена стадии обработки (`embedding` / `graph` / `vector` / `rerank` / `llm`).
- `token` — дельта токенов ответа.
- `done` — завершение: финальный текст, список источников и тайминги.
- `error` — сбой задачи с кодом ошибки.

```json
{
  "type": "done",
  "sources": [ { "source_url": "https://...", "relevance": 0.91 } ],
  "generation_time_s": 6.4
}
```

> Точный JSON-Schema событий стриминга (конверт события, типы `status`/`token`/`done`/`error`)
> зафиксирован в `docs/05_adr_log.md` ADR-016. Контракт асинхронного контура
> «202 Accepted + task_id + стриминг через WebSockets/SSE» — см. `docs/00`, `docs/history.md` Этап 6.

### 3.4. MCP-интеграция ИИ-агентов

Query API Gateway совмещает роль **MCP-шлюза** для автономных ИИ-агентов (например, Cursor),
которые читают граф знаний через тот же входной порт :8000 (см. `docs/00`, метка
«Query API Gateway / MCP-шлюз :8000»).

- **Транспорт:** MCP Protocol (JSON-RPC); запросы агентов проксируются в асинхронный контур запросов.
- **Ответ:** тот же поток, что у HTTP-клиентов — стриминг токенов через WebSockets / SSE.
- **Разница с HTTP:** MCP-шлюз ориентирован на машинных потребителей (агентский вызов, чтение
  графа/источников), а не на браузер.

> Набор MCP-tools (`graphrag.query`, `graphrag.get_active_domain`, `graphrag.resolve_term`,
> `graphrag.get_sources`) и правила доступа зафиксированы в `docs/05_adr_log.md` ADR-017.
> Политика перегрузок (rate-limiting) — `docs/security.md` §4.

---

## 4. Config API (:8001)

Полный состав, примеры и namespace-ключи — в `docs/04_services_config.md` §2–§3, §5 и `docs/06` §1.
Сводная карта:

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/v1/config/domain/active` | Имя активного профиля |
| GET | `/api/v1/config/domain/profiles` | Список доступных YAML-профилей |
| GET | `/api/v1/config/domain/profile/{name}` | Содержимое профиля |
| POST | `/api/v1/config/domain/validate` | Валидация структуры профиля: `400` — битый YAML/не-маппинг; иной результат — `200` с `{valid, errors}` (ошибки агрегированы, `422` не используется) |
| POST | `/api/v1/config/domain/profile` | Загрузка нового профиля — **фаза конфигуратора (M5+); в M0 не реализован** (профили читаются с read-only volume) |
| POST | `/api/v1/config/domain/activate` | Рантайм-переключение активного домена; конфликт `profile.name` vs `domain` — `422` |
| GET | `/api/v1/config/adapters` | Текущие адаптеры (значения ключей осей) |
| PUT | `/api/v1/config/adapters` | Смена адаптера на лету; тело — ключ оси → реализация, например `{"vector_store": "qdrant"}` |
| GET | `/api/v1/config/adapters/available` | Список доступных адаптеров (включая сторонние плагины) |

---

## 5. Ingestion API (:8002) — контракт (ADR-018)

Приём документов и управление фоновыми джобами индексации (9 этапов пайплайна,
`docs/02_pipeline_and_normalizer.md` §1: INGEST → CHUNK → EMBED → EXTRACT → NORMALIZE →
DEDUP → CONTRACT → VALIDATE → COMMIT).

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/api/v1/ingestion/documents` | Загрузка файла (`.txt` / `.md` / `.pdf` / `.json`) или URL; метаданные: `source_url`, `domain`, `doc_type`. Ответ `202` — `{job_id, status, created_at}` |
| GET | `/api/v1/ingestion/jobs` | Список джоб индексации (пагинация: `page`, `page_size`) |
| GET | `/api/v1/ingestion/jobs/{job_id}` | Статус джобы: `{job_id, status, stage}` |
| DELETE | `/api/v1/ingestion/jobs/{job_id}` | Отмена джобы (освобождение GPU) |

Жизненный цикл джобы: `queued → running (stage: INGEST|CHUNK|EMBED|EXTRACT|NORMALIZE|DEDUP|CONTRACT|VALIDATE|COMMIT) → succeeded | failed | cancelled`.

> Контракт и жизненный цикл джоб формально зафиксированы в `docs/05_adr_log.md` ADR-018;
> правила версионирования/удаления источников — ADR-014.

---

## 6. Glossary API (:8003) — внутренний протокол

Сервис используется пайплайном (слой 2 канонизации) и ретривером (resolve терминов), см. `docs/00` §1.

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/v1/glossary/{domain}` | Словарь (`glossary.{profile}.yaml`); домена нет — `404` |
| POST | `/api/v1/glossary/resolve` | Трансляция тега в канонический ряд: `{"term": "lg"}` → `{"canonical_name": "log", "variants": ["log", "lg", "ln"]}`. Домен — поле `domain` (опционально); иначе активный профиль по `GET /api/v1/config/domain/active` (fallback `it`) |
| POST | `/api/v1/glossary/validate` | Проверка уникальности словаря: `{"domain"?}` → `{"valid", "duplicates"}` |

> Расширенная glossary-валидация через `POST /api/v1/config/domain/validate`
> (секция `glossary`) — **запланирована (M1)**; в M0 config-validate проверяет
> только структуру профиля, уникальность тегов — `POST /api/v1/glossary/validate`.

---

## 7. Observable и прочее

- Метрики Prometheus — по порту `/metrics` каждого сервиса; перечень метрик — `docs/06_operations_and_risks.md` §2.
- Логи — JSON-логи в stdout → Promtail → Loki → Grafana (`docs/06` §2).
- Портовая карта сервисов — `docs/04_services_config.md` §1.