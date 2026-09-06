# v7: Агностификация CONCEPT v5.0 (итерация документации)

> **Отношение версий:** v7 — **итерация документации** поверх базы v5, выполненная перед сборкой
> прототипа. Актуальный SSOT остаётся в корне проекта: [CONCEPT.md](../../../CONCEPT.md) и
> [docs/](../../../docs/README.md).
>
> v7 делает `CONCEPT.md` **технологически-агностичным**: из спецификации убраны прямые привязки
> к инсталляции (`ohw_net`, карта портов, имена БД/моделей/адаптеров, тайминги, compose-профили,
> метрики, риски). В CONCEPT остаются только интерфейсы, инварианты и семантика — со ссылками
> на справочники.
>
> ⚠ Этот каталог — **архив**: консервирует конкретику, вычищенную из живой спецификации на Этапе 7.
> Актуальная картина архитектуры: [docs/00_hi_level_architecture.md](../../../docs/00_hi_level_architecture.md);
> порты/сеть — [docs/04_services_config.md](../../../docs/04_services_config.md);
> стек/профили/риски — [docs/06_operations_and_risks.md](../../../docs/06_operations_and_risks.md) и
> [docs/infrastructure_stack.md](../../../docs/infrastructure_stack.md);
> реализации адаптеров — [docs/adapters_specification.md](../../../docs/adapters_specification.md).

---

## Что хранится в этом каталоге

| Файл | Назначение |
|------|------------|
| [./CONCEPT.md](./CONCEPT.md) | Снапшот `CONCEPT.md` v5.0 **до** агностификации (Какой именно: базовая спецификация с картой портов, defaults namespace-ключей, базовыми реализациями адаптеров, таймингами и стеком метрик/композ-профилей/рисков). |

Снапшот снят 2026-09-05. Он служит **материалом для сборки прототипа**: конкретные имена
(Neo4jGraphStore, Neo4jVectorStore, QdrantVectorStore, OllamaAdapter, BgeM3ServiceAdapter,
BgeRerankerAdapter), тайминги (bge-m3 ~50 мс, Qwen 3–10 сек), порты (8000/8001/8002/8003/8004,
7687/7474, 11434), namespace-ключи и стек метрик/профилей/рисков сохранены здесь и должны
перекочевать в прототип-артефакты по мере реализации.

---

## Что именно было вычищено из CONCEPT (Этап 7)

Живой `CONCEPT.md` был приведён к технологической-агностичности. Вычищенный материал
(полностью сохранён в снапшоте выше):

- **§2.1** — базовые реализации `Neo4jGraphStore`, `Neo4jVectorStore`, `MemgraphGraphStore`,
  `QdrantVectorStore`.
- **§2.2** — реализации `OllamaAdapter` (HTTP :11434, Qwen 2.5 7B), `OpenAICompatibleAdapter`,
  `VllmAdapter`.
- **§2.3** — реализации `BgeM3ServiceAdapter` (:8004), `LocalSentenceTransformerAdapter`,
  `OpenAIEmbeddingsAdapter`.
- **§2.4** — реализации `BgeRerankerAdapter` (CPU), `NoOpRerankerAdapter`, `CohereRerankAdapter`.
- **§2.5** — конкретные defaults namespace-ключей и curl-пример на порт 8001.
- **§2.6** — пример `entry_points` (перенесён в `docs/adapters_guide.md`).
- **§4** — тайминги и имена моделей (bge-m3 ~50 мс, «Qwen 3–10 сек»), порт :8004 в EMBED, :8002
  в INGEST, chunk_size/popover-параметры.
- **§5** — тайминги (bge-m3 ~50 мс, Qwen 3–10 сек), базовая реализация bge-reranker-base.
- **§6.1** — карта портов и имя сети `ohw_net` (заменено списком сервисов без портов со ссылками).
- **§6.4** — конкретные имена глоссариев `glossary.{profile}.yaml`.
- **§6.5** — конкретные defaults namespace-ключей (включая `neo4j_uri`, имена моделей,
  `api_key`).
- **§8** — стек метрик (PromQL), compose-профили, таблица масштабирования, матрица рисков.

В живой CONCEPT оставлены: программные интерфейсы (GraphStoreProvider, VectorStoreProvider,
LLMInference, Embedder, Reranker), инварианты ядра, семантика namespace — со ссылками на
ADR/справочники.

---

## Как прототип потребляет этот архив

1. Реализации-кандидаты по интерфейсам берутся из снапшота `CONCEPT.md` §2 и
   `docs/adapters_specification.md` (§2.4).
2. Карта портов и сеть — из снапшота §6.1.
3. Тайминги/модели/профили/риски — из снапшота §4, §5, §8.

Этот каталог **не цитируется** в живых доках (архивные источники не ссылаются из рабочих
документов). Он существует только как материал для реализации.