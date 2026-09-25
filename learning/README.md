# learning — учебные материалы по областям платформы Dz4

Служебная папка монорепо для **изучения новых областей**, всплывающих при проработке
домашних заданий. Каждая тема — `*.md` с аналитикой «для себя» + ссылками на теорию.

Цель — не «показать что знаем», а честно закрыть пробелы: если решение из области
(например, инвалидация кэша) принимается в проекте, сначала разобраться в предмете.

В проектных врезках используются статусы **Target**, **Runtime**, **Planned**,
**Experiment** и **Superseded**: учебная теория не считается доказательством текущей
реализации. Полный порядок работы над методичками — в [WRITING_GUIDE.md](WRITING_GUIDE.md).

---

## Оглавление

| Файл | Область | Что разбирает |
|---|---|---|
| [WRITING_GUIDE.md](WRITING_GUIDE.md) | **Как писать методички** | Источники, статусы, жанры, структура, упражнения и чек-лист автора |
| [data_revision_analytics.md](data_revision_analytics.md) | **Свежесть данных в query-контуре** (Веха 4-хвост) | Что такое ревизия данных, bounded staleness, fingerprint, polling vs events; три развилки и рекомендации |
| [caching_freshness_learning.md](caching_freshness_learning.md) | **Кэширование и свежесть — методички** | Карта изучения: от «что такое кэш» до версионирования кэш-ключей; книги/курсы/доки по каждому куску теории |
| [caching_method_selection_learning.md](caching_method_selection_learning.md) | **Выбор метода кэширования** | Что/где/сколько кэшировать, семантика отказа (fail-open), конкурентность (stampede), экономика и критерий «когда кэш не нужен» — то, чего нет в методичке по свежести |
| [caching_overview_learning.md](caching_overview_learning.md) | **Кэширование: история, виды, семантический кэш LLM** (зонтичная) | От мемоизации и HTTP-кэшей до prompt caching: 5 уровней кэшей LLM-стека, анатомия семантического кэша Dz4 по коду, план ухода от HGETALL-скана |
| [data_freshness_overview_learning.md](data_freshness_overview_learning.md) | **Свежесть данных и консистентность** (зонтичная) | От 2PC и MESI до CDC и ревизии: хронология проблемы свежести, лестница моделей консистентности, 5 паттернов инвалидации с проекцией на Dz4, анатомия ревизии (ADR-014/025/026) |
| [distributed_consistency_learning.md](distributed_consistency_learning.md) | **Консистентность в распределённых системах** | Консистентность, bounded staleness, polling vs push, репликация — материалы |
| [hybrid_rag_dataflow_learning.md](hybrid_rag_dataflow_learning.md) | **Гибридный RAG: vector-first + context graph** | Payload и seeds, optional inline/offline graph experiment, fusion/assembly; справочники по heavy graph и parallel-axis |
| [hybrid_rag_overview_learning.md](hybrid_rag_overview_learning.md) | **Гибридный RAG: история, архитектуры, ингест** (зонтичная) | От TF-IDF (1972) до Agentic RAG: хронология, три эры поиска, семь семейств архитектур, эволюция ингеста v0-v3, паттерны хранения и их RAM-цена |
| [prototype_test_plan_learning.md](prototype_test_plan_learning.md) | **Испытания прототипа: что, зачем и почему меряем** (зонтичная) | От Cranfield experiments до TREC RAG: три уровня измерения, метрики поиска и генерации, атрибуция компонентов, четыре слоя артефактов прогона, проекция на eval-контур Dz4 |
| [free_resources_articles.md](free_resources_articles.md) | **Бесплатные статьи и курсы** | Альтернативы платным книгам (DDIA, System Design): бесплатные учебники, RFC, университетские лекции, видео, лабораторные |
| [llm_as_judge_learning.md](llm_as_judge_learning.md) | **LLM-as-a-judge** | История, pointwise/pairwise-подходы, reference-based/reference-free groundedness и практика judge в RAG |
| [judge_calibration_methods_learning.md](judge_calibration_methods_learning.md) | **Калибровка судьи** | Gold set, drift, детерминизм, Cohen κ/Spearman и протокол калибровки; отдельно отмечается, что часть процедур Dz4 ещё план |

---

## Как пользоваться

1. Открыл незнакомое решение в проекте (например, `epoch-bump query:sc:<rev>:<domain>`) —
   иди в `data_revision_analytics.md` за контекстом.
2. Понял, что термин/механизм «плавает» — возьми соответствующую методичку из
   `caching_freshness_learning.md` / `distributed_consistency_learning.md`, пройди
   по разделам от «для начала» к «углублённо».
3. Конспектируй прямо здесь (правь `*.md`), если хочется зафиксировать понимание.
4. Выбираешь **метод** кэширования (что кэшировать, где, сколько, как отказывать) —
   начни с `caching_method_selection_learning.md`.

Материалы подобраны под **практический контекст GraphRAG-прототипа Dz4**: в основном
методички (книги), не «статьи для галочки».