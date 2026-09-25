# Dz4: Гибридная RAG

Учебный прототип доменно-агностичной RAG-системы: графовый и векторный поиск
через независимые адаптеры (Neo4j — выбранный backend прототипа, не обязательная
архитектурная привязка), генерация ответа локальной LLM через llama.cpp, стриминг и источники.
Предметная область задаётся YAML-профилем и глоссарием (`it`, `library`, `cinema`).
Технологии изолированы адаптерами; это валидационный контур, не production-система.
Архитектурная концепция описывает также возможности, ещё не реализованные в прототипе.

Реализованы Config/Glossary, primitive Ingestion с optional graph enrichment,
асинхронный Query (Valkey → worker → SSE), vector-first hybrid retrieval,
Streamlit-демо, Topology Orchestrator и расширяемые чанкеры. Redis/Valkey и offline projection
настраиваются через Topology Configurator (`/api/v1/config/redis` и
`/api/v1/config/projection`); lease projection по умолчанию 300 секунд.

**Ограничения:** основной Compose использует `EMBEDDER=deterministic` и
`RERANKER=noop`. Хэш-эмбеддинги позволяют проверить интеграцию, но не качество
семантического поиска. Запуск профилей `embeddings`/`reranker` сам по себе
не переключает адаптеры клиентов: нужна согласованная настройка индексации и
поиска; при смене эмбеддера существующие документы необходимо переиндексировать.
Полноценные outbox, dual-generation и автоматическая переиндексация после смены
профиля/онтологии отложены на M6-Growth / pre-connectors; до этого этапа профиль
загруженного корпуса считается неизменным.
Демо загружает txt/md; бинарная загрузка PDF через этот API не предусмотрена.
Topology UI и конфигуратор — задел; monitoring в Compose закомментирован.

## 1. Требования и окружение

- Docker Engine 24+ / Compose v2.20+, доступ к Docker daemon и BuildKit.
- Для штатного CUDA-контура: Linux или Windows с WSL2, совместимый драйвер NVIDIA
  и поддержка GPU в Docker (NVIDIA Container Toolkit на Linux).
  На macOS данный CUDA Compose не запускается без изменения конфигурации.
- Ориентир для демо: 16+ ГБ RAM и 8+ ГБ VRAM, не гарантия запуска любого профиля.
  Потребление зависит от контекста LLM и одновременно загруженных моделей.
  Heap Neo4j ограничен 1G, page cache — 512M; это не лимит всей памяти контейнера.
- Интернет нужен для получения образов, зависимостей и весов при сборке.
  Генерация в штатном демо выполняется локально, без внешнего LLM-API.
- Для разработки: Python 3.11+ и uv либо dev-образ `ohw-python:3.13`;
  для запуска собранных сервисов Python на хосте не требуется.

---

## 2. Документация проекта
--------------------------------------------------------------------------------
Проектная документация организована в соответствии с принципом единого источника правды:

| Документ | Описание |
|----------|----------|
| [CONCEPT.md](./CONCEPT.md) | Главный архитектурный документ (концепция-база v5) |
| [docs/00_hi_level_architecture.md](./docs/00_hi_level_architecture.md) | Высокоуровневая архитектура (Mermaid, v6) + [PNG](./docs/assets/hi_level_architecture_v6.png) |
| [docs/01_ontology_and_domain_profile.md](./docs/01_ontology_and_domain_profile.md) | Онтология и спецификация Domain Profile |
| [docs/02_pipeline_and_normalizer.md](./docs/02_pipeline_and_normalizer.md) | Регламент Ingestion Pipeline и Normalizer v3 |
| [docs/03_retriever.md](./docs/03_retriever.md) | Стратегия ретривера и слияния контекста |
| [docs/04_services_config.md](./docs/04_services_config.md) | Конфигурация сервисов и Runtime API |
| [docs/05_adr_log.md](./docs/05_adr_log.md) | Журнал архитектурных решений (ADR) |
| [docs/06_operations_and_risks.md](./docs/06_operations_and_risks.md) | Operations, масштабирование и матрица рисков |
| [docs/glossary.md](./docs/glossary.md) | Глоссарий терминов |
| [docs/api_reference.md](./docs/api_reference.md) | API Reference: контракты Query / Config / Ingestion / Glossary |
| [docs/data_model.md](./docs/data_model.md) | Схема данных: узлы, рёбра, свойства, индексы |
| [docs/infrastructure_stack.md](./docs/infrastructure_stack.md) | Технологический стек: контейнеры, языки, СУБД, профили, лицензии |
| [docs/expert_reviews.md](./docs/expert_reviews.md) | Экспертные оценки концепции |
| [docs/history.md](./docs/history.md) | История разработки концепции |
| [docs/adapters_specification.md](./docs/adapters_specification.md) | Подробная спецификация слоя адаптеров (v5) |
| [docs/adapters_guide.md](./docs/adapters_guide.md) | Пошаговая инструкция подключения внешних систем через слой адаптеров |
| [docs/security.md](./docs/security.md) | Безопасность и аутентификация (X-API-Key, роли, транспорт, лимиты) |
| [docs/operations_requirements.md](./docs/operations_requirements.md) | Эксплуатационные требования (бэкап, retention, reconciliation, аудит, A/B) |
| [docs/invariants.md](./docs/invariants.md) | Инварианты платформы: обязательные контракты по слоям (L1–L5) |
| [docs/prototype_requirements.md](./docs/prototype_requirements.md) | Требования к прототипу: цель, границы, стек, вехи, eval-гейт |
| [docs/test_plan.md](./docs/test_plan.md) | План испытаний прототипа: уровни метрик, артефакты прогона, режимы, границы измерения |
| [docs/web_layer_replacement.md](./docs/web_layer_replacement.md) | Как менять веб-слой (Query API Gateway) без переделки ядра |
| [docs/demo_runbook.md](./docs/demo_runbook.md) | Runbook демо-контура: подъём стека, UI `:8503`, сценарии в браузере, прогон e2e |
| [docs/demo_user_guide.md](./docs/demo_user_guide.md) | Руководство пользователя демо: требования, скачивание, запуск, сценарий, устройство |

**Архитектурная концепция (6 документов):**
1. [01. Онтология и Спецификация Domain Profile](./docs/01_ontology_and_domain_profile.md) — описание узлов, связей и YAML-конфигуратора.
2. [02. Регламент Primitive Ingestion и optional enrichment](./docs/02_pipeline_and_normalizer.md) — документ, chunks, vectors и разреженный context graph.
3. [03. Стратегия Ретривера и Слияния контекста](./docs/03_retriever.md) — 7 шагов цикла генерации, реранкер и вытеснение лимитов токенов.
4. [04. Конфигурация Сервисов и Рантайм API](./docs/04_services_config.md) — изолированная сеть ohw_net, эндпоинты Config Service и глоссарии.
5. [05. Журнал архитектурных решений (ADR)](./docs/05_adr_log.md) — обоснование выбора стека и платформенного подхода.
6. [06. Регламент Operations, Масштабирования и Рисков](./docs/06_operations_and_risks.md) — неймспейсы конфигов, метрики Prometheus, Docker-профили и матрица рисков.

---

## 3. Быстрый старт и развертывание
--------------------------------------------------------------------------------

Команды ниже — для Bash (Linux/WSL). Проект в этой рабочей среде расположен
в `d:\Otus\ohw\Dz4`, в WSL — `/mnt/d/Otus/ohw/Dz4`.
В другой копии репозитория замените абсолютные пути.

### Шаг 1: Проверка окружения
Требуются доступ к Docker daemon и поддержка NVIDIA GPU для полного демо.
```bash
# Проверка версий Docker
docker --version
docker compose version

# Проверка доступности GPU внутри Docker
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

### Шаг 2: Запуск базовой инфраструктуры
```bash
# Config/Glossary и Neo4j — этот контур не требует GPU
docker compose --file /mnt/d/Otus/ohw/Dz4/prototype/infra/compose.yaml --profile config --profile graph up -d --build --wait

# Проверка статуса контейнеров
docker compose --file /mnt/d/Otus/ohw/Dz4/prototype/infra/compose.yaml --profile config --profile graph ps
```

### Шаг 3: Остановка сервисов
```bash
# Остановка базового контура с сохранением данных
docker compose --file /mnt/d/Otus/ohw/Dz4/prototype/infra/compose.yaml --profile config --profile graph down

# Остановка полного демо с сохранением данных
docker compose --file /mnt/d/Otus/ohw/Dz4/prototype/infra/compose.yaml --profile config --profile graph --profile ingestion --profile llm down
# Для опциональных сервисов добавьте их профили. down -v УДАЛЯЕТ данные volumes.
```

> Артефакты прототипа (compose, профили доменов, глоссарии, инфраструктура) живут
> в `prototype/` отдельно от документации: README/docs — стабильный основной элемент,
> прототип — временный валидационный контур этапа M0–M4.

### Демо-контур (браузерный UI `:8503`)

Сквозной контур на уровне API работает с M2; браузерная точка входа — Streamlit-демо
`:8503` (профиль `llm`): загрузка txt/md → стадии INGEST → запрос со стримингом
ответа и `sources` → soft-delete документа. Инструкция — [docs/demo_runbook.md](./docs/demo_runbook.md).

```bash
# У ingestion-api нет build-секции в Compose: подготовьте образ отдельно
docker build --file /mnt/d/Otus/ohw/Dz4/prototype/Dockerfile --tag ohw/ingestion-service:prototype /mnt/d/Otus/ohw/Dz4/prototype
# Остальные образы приложений и LLM собираются через --build
docker compose --file /mnt/d/Otus/ohw/Dz4/prototype/infra/compose.yaml --profile config --profile graph --profile ingestion --profile llm up -d --build --wait

# UI: http://localhost:8503
# Тесты и прямой запуск e2e описаны в README прототипа.
```

---

### Сервисы и модель

| Сервис | Порт хоста | Профиль Compose |
|---|---|---|
| Config / Glossary | 8001 / 8003 | `config` |
| Neo4j HTTP / Bolt | 7474 / 7687 | `graph` |
| Ingestion API | 8002 | `ingestion` |
| Query API / worker | 8000 / без порта | `llm` |
| Valkey / llama.cpp / Demo UI | 6379 / 8080 / 8503 | `llm` |
| Topology Orchestrator | 8005 | `topology`, опционально |
| BGE-M3 embeddings | 8004 | `embeddings`, опционально, GPU |
| BGE reranker | 8006 | `reranker`, опционально, CPU |

LLM-образ `ohw/llm:prototype` уже собирается Compose из
[Dockerfile.llm](./prototype/Dockerfile.llm). При сборке загружается
`Qwen2.5-Coder-7B-Instruct-abliterated-Q4_K_M.gguf` из
`bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF`.
Закреплена commit-ревизия HF, проверяется SHA-256; runtime —
`ghcr.io/ggml-org/llama.cpp:server-cuda-b10853`.
**Локальный GGUF и bind-mount модели не требуются**: веса внутри образа.
Это не отменяет mounts/volumes профилей и данных остальных сервисов.

При сохранённом действительном build-кэше повторная загрузка весов обычно
не требуется; полная пересборка может снова потребовать сеть.
Для смены модели согласуйте `HF_REPO`, `HF_REVISION`, `HF_FILE`, `HF_SHA256`
(build-args) и путь `--model` в Compose. BuildKit-secret для HF-токена
текущим Dockerfile не подключён, поддержка приватных моделей не готова.
Контекст LLM — 8192; `--n-gpu-layers 99` запрашивает размещение слоёв на GPU.

**Безопасность:** тестовые значения `GRAPH_AUTH_API_KEY=changeme` и
`NEO4J_PASSWORD=graphrag` замените секретами в окружении перед использованием
вне изолированного демо. Порты опубликованы не только на loopback;
llama.cpp и Valkey не защищены API-ключом приложений. Не выставляйте стек
в недоверенную сеть: необходима отдельная настройка сетевого доступа и защиты.

### Настройки и проверка

- `INGEST_CHUNKER`: `sliding_window` (по умолчанию), `structure_aware`,
  интеграции `langchain`/`llamaindex` и entry-point плагины.
  Внешние библиотеки/плагины требуют установки; размер окна/перекрытие —
  `INGEST_CHUNK_SIZE=512`, `INGEST_CHUNK_OVERLAP=64` (слова).
  См. [руководство по чанкерам](./docs/chunkers_guide.md).
- `RETRIEVAL_GRAPH_ENABLED=true|false` переключает графовую ось Query Worker.
- Профиль `topology` добавляет API переключения адаптеров; Query Worker
  опрашивает ревизию и пересобирает pipeline. Это не автоматическая
  перенастройка уже сохранённых векторов или всех клиентов.

Тесты, линт, типизация и прямой запуск e2e — в
[README прототипа](./prototype/README.md).
E2E-скрипт сейчас вычисляет ошибочный вложенный путь `prototype/prototype`
и рассчитан на удаление volumes по умолчанию. До исправления используйте
pytest напрямую против отдельного тестового стека; команды запуска из
runbook следует сверять с этим README.

## 4. Управление доменами (API Спецификация)
--------------------------------------------------------------------------------
Рантайм-переключение предметной области выполняется через Config Service (Порт `8001`).

Запросы требуют `X-API-Key`. Примеры для Bash используют ключ из окружения
или тестовое значение `changeme`.

```bash
KEY="${GRAPH_AUTH_API_KEY:-changeme}"
curl -sS -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/yaml' --data-binary @/mnt/d/Otus/ohw/Dz4/prototype/domain_profiles/domain_profile.library.yaml http://localhost:8001/api/v1/config/domain/validate
# Валидный профиль: {"valid":true,"errors":[]}

curl -sS -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"domain":"library"}' http://localhost:8001/api/v1/config/domain/activate
# HTTP 200: {"domain":"library","activated":true}
```

Валидация структуры возвращает HTTP 200 с `valid` и `errors`, даже если профиль
невалиден. HTTP 400 — сломанный YAML или не-маппинг; 401 — неверный/отсутствующий
ключ; 404 — профиль не найден; 422 при активации — отсутствующее поле `domain`,
невалидный профиль или несовпадение `profile.name` с доменом.
Активация сохраняется в SQLite Config Service; она не накатывает constraints Neo4j.

---

## 5. Troubleshooting (Устранение неполадок)
--------------------------------------------------------------------------------
- **Docker daemon недоступен:** запустите Docker Engine / Docker Desktop с Linux-контейнерами.
- **Neo4j завершается с 137:** проверьте `docker inspect ohw-neo4j` и поле
  `State.OOMKilled`, затем доступную RAM и лимиты JVM. Код 137 сам по себе
  не доказывает нехватку памяти.
- **LLM не готова:** `docker logs ohw-llm`; проверьте VRAM и доступность GPU.
  Ошибки скачивания HF/SHA-256 видны в журнале сборки, не runtime-логе.
- **Образ ingestion-service отсутствует:** выполните отдельный `docker build`
  из раздела демо — у сервиса нет build-секции.
- **Ответ пустой или нерелевантный:** проверьте статус INGEST, домен и источники;
  штатный `deterministic`-эмбеддер не оценивает семантическую близость.

---

## 6. Лицензия и Поддержка
--------------------------------------------------------------------------------
* **Лицензия:** Внутренний учебный проект (Internal Academic Use Only). Ядро опирается на Neo4j Community (GPLv3) и Qwen 2.5 (Apache 2.0).
* **Обратная связь:** По вопросам работы пайплайнов и обнаруженным багам создавайте Issue в текущем репозитории или пишите команде инженеров (координаты в файле `CONTRIBUTING.md`).
