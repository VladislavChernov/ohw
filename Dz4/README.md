# Dz4: Мультиязычная Платформа Enterprise GraphRAG (v6 — итерация поверх базы v5)
================================================================================

Гибридная (графовая + векторная) RAG-платформа корпоративного уровня. 
Предназначена для семантического поиска, анализа контрактов и выявления логических 
противоречий в документации без использования внешних API. 

Движок является полностью доменно-агностичным. Настройка на конкретную предметную 
область (ИТ, кино, литература) осуществляется динамической подгрузкой Domain Profile (YAML).
Слой адаптеров изолирует ядро от конкретных технологий (Neo4j, Ollama, bge-m3).

---

## 1. Минимальные системные требования
--------------------------------------------------------------------------------
* **ОС:** Linux (Ubuntu 22.04+), macOS (M1/M2/M3), Windows 11 (через WSL2).
* **CPU:** Минимум 4 ядра (рекомендуется 8 ядер для эффективного реранкинга).
* **RAM (Системная ОЗУ):** Строго от 16 ГБ. (Neo4j зажат в лимит 1.5 ГБ JVM).
* **GPU (Видеопамять):** NVIDIA RTX 2070 Super и выше (Минимум 8 ГБ VRAM).
* **Зависимости:** Docker Engine v24.0+, Docker Compose v2.20+, Python 3.11+ (slim).

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
| [docs/05_adr_log.md](./docs/05_adr_log.md) | Журнал архитектурных решений (ADR-001 - ADR-020) |
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
| [docs/web_layer_replacement.md](./docs/web_layer_replacement.md) | Как менять веб-слой (Query API Gateway) без переделки ядра |

**Архитектурная концепция (6 документов):**
1. [01. Онтология и Спецификация Domain Profile](./docs/01_ontology_and_domain_profile.md) — описание узлов, связей и YAML-конфигуратора.
2. [02. Регламент Ingestion Pipeline и Normalizer v3](./docs/02_pipeline_and_normalizer.md) — 9 этапов загрузки, контекстная канонизация Big-O и валидатор.
3. [03. Стратегия Ретривера и Слияния контекста](./docs/03_retriever.md) — 7 шагов цикла генерации, реранкер и вытеснение лимитов токенов.
4. [04. Конфигурация Сервисов и Рантайм API](./docs/04_services_config.md) — изолированная сеть ohw_net, эндпоинты Config Service и глоссарии.
5. [05. Журнал архитектурных решений (ADR)](./docs/05_adr_log.md) — обоснование выбора стека и платформенного подхода (ADR-001 - ADR-020).
6. [06. Регламент Operations, Масштабирования и Рисков](./docs/06_operations_and_risks.md) — неймспейсы конфигов, метрики Prometheus, Docker-профили и матрица рисков.

---

## 3. Быстрый старт и развертывание
--------------------------------------------------------------------------------

### Шаг 1: Проверка окружения и прав доступа
Для запуска требуются права локального администратора (`sudo` на Linux) и поддержка CUDA.
```bash
# Проверка версий Docker
docker --version
docker compose version

# Проверка доступности GPU внутри Docker
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

### Шаг 2: Запуск базовой инфраструктуры
```bash
# Запуск конфигурационных сервисов и СУБД Neo4j
docker compose --file prototype/infra/compose.yaml --profile config --profile graph up -d

# Проверка успешности запуска контейнеров
docker compose --file prototype/infra/compose.yaml ps
```

### Шаг 3: Остановка сервисов
```bash
# Остановка с сохранением скачанных моделей и весов в volumes
docker compose --file prototype/infra/compose.yaml --profile config --profile graph down

# Полная очистка окружения (удаление всех контейнеров проекта)
docker compose --file prototype/infra/compose.yaml down --remove-orphans
```

> Артефакты прототипа (compose, профили доменов, глоссарии, инфраструктура) живут
> в `prototype/` отдельно от документации: README/docs — стабильный основной элемент,
> прототип — временный валидационный контур этапа M0–M4.

---

## 4. Управление доменами (API Спецификация)
--------------------------------------------------------------------------------
Рантайм-переключение предметной области выполняется через Config Service (Порт `8001`).

### Пример 1: Валидация нового профиля домена
* **Запрос:** `POST http://localhost:8001/api/v1/config/domain/validate`
* **Тело запроса:** Контент файла `prototype/domain_profiles/domain_profile.library.yaml`

### Пример 2: Активация домена
* **Запрос:** `POST http://localhost:8001/api/v1/config/domain/activate`
* **Тело запроса:** `{"profile": "library"}`
* **Успешный ответ (Код 200 OK):**
```json
{
  "status": "activated",
  "active_domain": "library",
  "previous_domain": "it",
  "loaded_at": "2026-09-04T22:50:00Z"
}
```

### Коды возможных ошибок API:
* `400 Bad Request` — Ошибка синтаксиса YAML-профиля или нарушение уникальности `unique_key`.
* `422 Unprocessable Entity` — Ошибка валидации онтологии: связи в `edge_types` (from/to) ссылаются на несуществующие типы узлов.
* `500 Internal Server Error` — Сбой подключения к графовой СУБД при попытке наката constraints.

---

## 5. Troubleshooting (Устранение неполадок)
--------------------------------------------------------------------------------
* **Проблема:** Контейнер Neo4j падает сразу после старта с кодом `137`.
  * *Причина:* Защитник системы (OOM Killer) принудительно убивает процесс из-за нехватки ОЗУ.
  * *Решение:* Закройте тяжелые локальные приложения (браузеры, IDE) либо уменьшите параметры памяти JVM в `prototype/infra/compose.yaml`.
* **Проблема:** Модели долго качаются или пайплайн выдает таймаут сети.
  * *Решение:* Убедитесь, что контейнеры находятся в сети `ohw_net`. Логируйте статус загрузки моделей через `docker logs ohw-ollama`.

---

## 6. Лицензия и Поддержка
--------------------------------------------------------------------------------
* **Лицензия:** Внутренний учебный проект (Internal Academic Use Only). Ядро опирается на Neo4j Community (GPLv3) и Qwen 2.5 (Apache 2.0).
* **Обратная связь:** По вопросам работы пайплайнов и обнаруженным багам создавайте Issue в текущем репозитории или пишите команде инженеров (координаты в файле `CONTRIBUTING.md`).
