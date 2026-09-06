# Документация GraphRAG платформы

## Обзор структуры документации

Эта директория содержит техническую документацию проекта GraphRAG.
Файлы лежат плоско (без вложенных слоёв); логическая группировка — только этот индекс.
Нумерация `00`–`06` — SSOT-порядок (якоря, на которые ссылаются остальные документы).

### Концепт и архитектура

| Документ | Описание |
|----------|----------|
| [CONCEPT.md](../CONCEPT.md) | Главная спецификация (SSOT-база v5, технологически-агностичная, v7-редакция) |
| [00_hi_level_architecture.md](./00_hi_level_architecture.md) | Высокоуровневая архитектура системы (Mermaid, v6) |
| [invariants.md](./invariants.md) | Инварианты платформы: обязательные контракты по слоям (L1–L5) |
| [expert_reviews.md](./expert_reviews.md) | Экспертные оценки концепции и закрытые пробелы |
| [history.md](./history.md) | История разработки концепции (этапы, архивы v4/v6/v7) |

### Данные и предметная область

| Документ | Описание |
|----------|----------|
| [01_ontology_and_domain_profile.md](./01_ontology_and_domain_profile.md) | Онтология графа знаний и спецификация Domain Profile |
| [data_model.md](./data_model.md) | Схема данных: узлы, рёбра, свойства, индексы |
| [glossary.md](./glossary.md) | Глоссарий терминов |

### Процессы

| Документ | Описание |
|----------|----------|
| [02_pipeline_and_normalizer.md](./02_pipeline_and_normalizer.md) | Регламент Ingestion Pipeline (9 этапов) и Normalizer v3 |
| [03_retriever.md](./03_retriever.md) | Стратегия ретривера и слияния контекста |

### API и сервисы

| Документ | Описание |
|----------|----------|
| [04_services_config.md](./04_services_config.md) | Конфигурация сервисов, порты и Runtime API |
| [api_reference.md](./api_reference.md) | API Reference: контракты Query / Config / Ingestion / Glossary |
| [adapters_specification.md](./adapters_specification.md) | Подробная спецификация слоя адаптеров (v5) |
| [adapters_guide.md](./adapters_guide.md) | Пошаговая инструкция подключения внешних систем через слой адаптеров |

### Операции и безопасность

| Документ | Описание |
|----------|----------|
| [06_operations_and_risks.md](./06_operations_and_risks.md) | Масштабирование, метрики, профили и матрица рисков |
| [operations_requirements.md](./operations_requirements.md) | Эксплуатационные требования: бэкап, retention, reconciliation, аудит, A/B |
| [infrastructure_stack.md](./infrastructure_stack.md) | Технологический стек: контейнеры, языки, СУБД, профили, лицензии |
| [security.md](./security.md) | Безопасность: аутентификация, роли, транспорт, секреты, лимиты |

### Решения

| Документ | Описание |
|----------|----------|
| [docs/05_adr_log.md](./05_adr_log.md) | Журнал архитектурных решений (ADR-001 - ADR-020) |

### Прототип и следующие этапы

| Документ | Описание |
|----------|----------|
| [prototype_requirements.md](./prototype_requirements.md) | Требования к прототипу: цель, границы, стек, вехи, eval-гейт, заглушки User Guide / Runbook |
| [web_layer_replacement.md](./web_layer_replacement.md) | Замена веб-слоя (Query API Gateway): контракты границы, процедура, контрактные тесты |

### Артефакты прототипа (вне docs/)

| Путь | Содержимое |
|------|-----------|
| [prototype/](../prototype/) | Временный валидационный контур M0–M4: `infra/compose.yaml`, `infra/config/`, `infra/eval/`, `domain_profiles/`, `infra_topology.yaml`, `src/` |
| [openspec/](../openspec/) | Спецификации изменений (spec-first): `project.md` + бандлы по вехам (`changes/add-prototype-m0-services/`) |
| [.opencode/](../.opencode/) | Harness: скиллы `openspec-propose`/`openspec-apply`, агент `reviewer`, команда `/review` |

### Медиа-файлы

| Директория | Содержимое |
|------------|-----------|
| [assets](./assets/) | Актуальная схема архитектуры: [hi_level_architecture_v6.png](./assets/hi_level_architecture_v6.png) |

---

## Как пользоваться документацией

1. **Новому участнику проекта:** Начните с [README.md](../README.md) → [CONCEPT.md](../CONCEPT.md) → [glossary.md](./glossary.md)
2. **Архитектору:** [CONCEPT.md](../CONCEPT.md) → [00_hi_level_architecture.md](./00_hi_level_architecture.md) → [05_adr_log.md](./05_adr_log.md)
3. **Разработчику:** [02_pipeline_and_normalizer.md](./02_pipeline_and_normalizer.md) → [03_retriever.md](./03_retriever.md) → [04_services_config.md](./04_services_config.md)
4. **DevOps инженеру:** [06_operations_and_risks.md](./06_operations_and_risks.md) → [04_services_config.md](./04_services_config.md)
5. **Тем, кто подключает свою систему:** [adapters_specification.md](./adapters_specification.md) → [adapters_guide.md](./adapters_guide.md)
6. **Тем, кто пишет код/интегрирует APIs:** [api_reference.md](./api_reference.md) → [data_model.md](./data_model.md) → [04_services_config.md](./04_services_config.md)
7. **DevOps / эксплуатации:** [infrastructure_stack.md](./infrastructure_stack.md) → [06_operations_and_risks.md](./06_operations_and_risks.md) → [operations_requirements.md](./operations_requirements.md)
8. **Инженеру по безопасности:** [security.md](./security.md) → [api_reference.md](./api_reference.md) §1–§2, [06_operations_and_risks.md](./06_operations_and_risks.md) §2
9. **При ревью кода/архитектуры:** [invariants.md](./invariants.md) — обязательный чек-лист контрактов → [05_adr_log.md](./05_adr_log.md) (обоснование)
10. **Перед началом прототипирования:** [prototype_requirements.md](./prototype_requirements.md) — цель, границы, вехи, стек и eval-гейт; конкретика — в [docs/history/v7/CONCEPT.md](./history/v7/CONCEPT.md)
11. **Тем, кто собирает прототип:** конкретика-кандидаты в [docs/history/v7/CONCEPT.md](./history/v7/CONCEPT.md) (архивная итерация), контракты — в [api_reference.md](./api_reference.md) и [adapters_specification.md](./adapters_specification.md)

---

## Связи между документами

```
README.md
    ↓
CONCEPT.md (главная концепция)
    ↓  (обеспечивается: invariants.md — контракты L1–L5)
    ├── docs/00_hi_level_architecture.md        (концепт/архитектура)
    ├── docs/01_ontology_and_domain_profile.md  (данные/онтология)
    ├── docs/02_pipeline_and_normalizer.md      (процессы/ingestion)
    ├── docs/03_retriever.md                    (процессы/ретривер)
    ├── docs/04_services_config.md              (API/сервисы)
    ├── docs/05_adr_log.md                      (решения)
    ├── docs/06_operations_and_risks.md         (операции)
    │
    ├── docs/data_model.md, docs/glossary.md    (данные)
    ├── docs/api_reference.md,
    │   docs/adapters_specification.md,
    │   docs/adapters_guide.md                  (API/адаптеры)
    ├── docs/infrastructure_stack.md,
    │   docs/operations_requirements.md,
    │   docs/security.md                        (операции/безопасность)
    ├── docs/expert_reviews.md,
    │   docs/invariants.md,
    │   docs/history.md                         (контроль и история)
    ├── docs/prototype_requirements.md          (следующий этап: прототип)
    └── docs/history/v7/ (архив конкретики v5, вычищенной на Этапе 7)
```

> **Примечание:** Конкретика инсталляции (порты, имена БД/моделей/адаптеров, network, профили, метрики)
> вынесена из CONCEPT на Этапе 7 (агностификация) в справочники — `04`, `06`, `infrastructure_stack`,
> `adapters_specification`, `api_reference`. Инварианты — `invariants.md`. Материал для сборки прототипа —
> `docs/history/v7/CONCEPT.md`.