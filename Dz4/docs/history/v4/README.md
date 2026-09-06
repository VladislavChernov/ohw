# Документация GraphRAG платформы (v4.0 — архив)

## Обзор структуры документации

Эта директория содержит техническую документацию проекта GraphRAG версии 4.0.

### Основные документы

| № | Документ | Описание |
|---|----------|----------|
| 00 | [hi_level_architecture.md](./00_hi_level_architecture.md) | Высокоуровневая блок-схема архитектуры системы |
| 01 | [01_ontology_and_domain_profile.md](./01_ontology_and_domain_profile.md) | Онтология графа знаний и спецификация Domain Profile |
| 02 | [02_pipeline_and_normalizer.md](./02_pipeline_and_normalizer.md) | Регламент Ingestion Pipeline и Normalizer v3 |
| 03 | [03_retriever.md](./03_retriever.md) | Стратегия ретривера и слияния контекста |
| 04 | [04_services_config.md](./04_services_config.md) | Конфигурация сервисов и Runtime API |
| 05 | [05_adr_log.md](./05_adr_log.md) | Журнал архитектурных решений (ADR-001 - ADR-011) |
| 06 | [06_operations_and_risks.md](./06_operations_and_risks.md) | Operations, масштабирование и матрица рисков |

### Справочные документы

| Документ | Описание |
|----------|----------|
| [glossary.md](./glossary.md) | Глоссарий терминов |
| [expert_reviews.md](./expert_reviews.md) | Экспертные оценки концепции |
| [history.md](./history.md) | История разработки концепции |
| [review.md](./review.md) | Отзывы и рекомендации по улучшению README.md |

### Медиа-файлы

| Директория | Содержимое |
|------------|-----------|
| [assets](./assets/) | Графические материалы и схемы (PDF, SVG) |

---

## Как пользоваться документацией

1. **Новому участнику проекта:** Начните с [README.md](../README.md) → [CONCEPT.md](../CONCEPT.md) → [glossary.md](./glossary.md)
2. **Архитектору:** [CONCEPT.md](../CONCEPT.md) → [00_hi_level_architecture.md](./00_hi_level_architecture.md) → [05_adr_log.md](./05_adr_log.md)
3. **Разработчику:** [02_pipeline_and_normalizer.md](./02_pipeline_and_normalizer.md) → [03_retriever.md](./03_retriever.md) → [04_services_config.md](./04_services_config.md)
4. **DevOps инженеру:** [06_operations_and_risks.md](./06_operations_and_risks.md) → [04_services_config.md](./04_services_config.md)

---

## Связи между документами

```
README.md
    ↓
CONCEPT.md (главная концепция)
    ├── docs/00_hi_level_architecture.md
    ├── docs/01_ontology_and_domain_profile.md
    ├── docs/02_pipeline_and_normalizer.md
    ├── docs/03_retriever.md
    ├── docs/04_services_config.md
    ├── docs/05_adr_log.md
    ├── docs/06_operations_and_risks.md
    ├── docs/glossary.md
    ├── docs/expert_reviews.md
    └── docs/history.md
```