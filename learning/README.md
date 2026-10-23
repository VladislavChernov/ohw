# learning — учебные материалы по областям

Служебная папка монорепо для **изучения новых областей**, всплывающих при
проработке домашних заданий. Каждая тема — `*.md` с аналитикой «для себя» и
ссылками на теорию, но не замена проектной документации.

Цель — не «показать что знаем», а честно закрыть пробелы: если решение из
области принимается в проекте, сначала разобраться в предмете.

В корне `learning/` лежат только этот индекс и
[WRITING_GUIDE.md](WRITING_GUIDE.md). Сами методички сгруппированы по областям:

| Область | Что внутри |
|---|---|
| [HybridRag/](HybridRag/) | RAG, кэширование, свежесть данных, консистентность, LLM-as-judge, испытания прототипа, личный harness |
| [Security/](Security/) | встраивание security wall в агентские среды, red-team агентских сред |

В проектных врезках используются статусы **Target**, **Runtime**, **Planned**,
**Experiment**, **Superseded** и **Reference**: учебная теория не считается
доказательством текущей реализации. Полный порядок работы над методичками — в
[WRITING_GUIDE.md](WRITING_GUIDE.md).

---

## Security

| Файл | Область | Что разбирает |
|---|---|---|
| [security_wall_integration_learning.md](Security/security_wall_integration_learning.md) | **Встраивание security wall** | Wall vs proxy, уровни L0–L6, своя среда vs чужой инструмент, model gateway, hooks, tool policy, sandbox |
| [red_team_integration_learning.md](Security/red_team_integration_learning.md) | **Red-team агентских сред** | Threat model, adapter, oracle, `n`, evidence, отчётность, black-box vs gray-box |

## HybridRag

| Файл | Область | Что разбирает |
|---|---|---|
| [data_revision_analytics.md](HybridRag/data_revision_analytics.md) | **Свежесть данных в query-контуре** (Веха 4-хвост) | Что такое ревизия данных, bounded staleness, fingerprint, polling vs events; три развилки и рекомендации |
| [caching_freshness_learning.md](HybridRag/caching_freshness_learning.md) | **Кэширование и свежесть — методички** | Карта изучения: от «что такое кэш» до версионирования кэш-ключей; книги/курсы/доки по каждому куску теории |
| [caching_method_selection_learning.md](HybridRag/caching_method_selection_learning.md) | **Выбор метода кэширования** | Что/где/сколько кэшировать, семантика отказа (fail-open), конкурентность (stampede), экономика и критерий «когда кэш не нужен» |
| [caching_overview_learning.md](HybridRag/caching_overview_learning.md) | **Кэширование: история, виды, семантический кэш LLM** (зонтичная) | От мемоизации и HTTP-кэшей до prompt caching: 5 уровней кэшей LLM-стека, анатомия семантического кэша Dz4 по коду, план ухода от HGETALL-скана |
| [data_freshness_overview_learning.md](HybridRag/data_freshness_overview_learning.md) | **Свежесть данных и консистентность** (зонтичная) | От 2PC и MESI до CDC и ревизии: хронология проблемы свежести, лестница моделей консистентности, 5 паттернов инвалидации с проекцией на Dz4 |
| [distributed_consistency_learning.md](HybridRag/distributed_consistency_learning.md) | **Консистентность в распределённых системах** | Консистентность, bounded staleness, polling vs push, репликация — материалы |
| [hybrid_rag_dataflow_learning.md](HybridRag/hybrid_rag_dataflow_learning.md) | **Гибридный RAG: vector-first + context graph** | Payload и seeds, optional inline/offline graph experiment, fusion/assembly; справочники по heavy graph и parallel-axis |
| [hybrid_rag_overview_learning.md](HybridRag/hybrid_rag_overview_learning.md) | **Гибридный RAG: история, архитектуры, ингест** (зонтичная) | От TF-IDF (1972) до Agentic RAG: хронология, три эры поиска, семь семейств архитектур, эволюция ингеста v0-v3 |
| [entity_identity_reconciliation_learning.md](HybridRag/entity_identity_reconciliation_learning.md) | **Идентичность сущности и примирение данных** | Кто выдаёт идентификатор (ISO 11179, SKOS), уровни доверия curated/proposed/rejected, abstention и NIL, неразрешённые ссылки; разбор развилки в Dz4 |
| [prototype_test_plan_learning.md](HybridRag/prototype_test_plan_learning.md) | **Испытания прототипа: что, зачем и почему меряем** (зонтичная) | От Cranfield experiments до TREC RAG: три уровня измерения, метрики поиска и генерации, атрибуция компонентов, четыре слоя артефактов прогона |
| [free_resources_articles.md](HybridRag/free_resources_articles.md) | **Бесплатные статьи и курсы** | Альтернативы платным книгам (DDIA, System Design): бесплатные учебники, RFC, университетские лекции, видео, лабораторные |
| [llm_as_judge_learning.md](HybridRag/llm_as_judge_learning.md) | **LLM-as-a-judge** | История, pointwise/pairwise-подходы, reference-based/reference-free groundedness и практика judge в RAG |
| [judge_calibration_methods_learning.md](HybridRag/judge_calibration_methods_learning.md) | **Калибровка судьи** | Gold set, drift, детерминизм, Cohen κ/Spearman и протокол калибровки; отдельно отмечается, что часть процедур Dz4 ещё план |
| [personal_harness.md](HybridRag/personal_harness.md) | **Личный harness** | Операционные правила владельца: как принимаются решения и чего ожидать от инструментов |

---

## Как пользоваться

1. Открыл незнакомое решение в проекте — найди область выше и иди в
   соответствующий файл за контекстом.
2. Понял, что термин/механизм «плавает» — возьми методичку с подходящим
   уровнем: обзор, gap-closing guide или instrument manual.
3. Конспектируй прямо здесь (правь `Security/*.md` или `HybridRag/*.md`), если
   хочется зафиксировать понимание.
4. Перед добавлением нового файла прочитай [WRITING_GUIDE.md](WRITING_GUIDE.md)
   и создай его в поддиректории своей области, а не в корне.

Материалы `HybridRag/` подобраны под практический контекст GraphRAG-прототипа
Dz4. Материалы `Security/` — под агентский стенд Dz5, но описывают общий
подход, а не только конкретную реализацию.
