# Документация: Схема данных (Data Model)

> **Версия:** v6 (итерация поверх базы v5)
> **Последнее обновление:** 2026-09-05
>
> Источники: `CONCEPT.md` §4, `docs/01_ontology_and_domain_profile.md`, `docs/02_pipeline_and_normalizer.md`,
> `docs/glossary.md` (раздел Data Model), `docs/expert_reviews.md` (рекомендация «Схема узлов и связей»).

## 1. Два уровня схемы

- **Платформенный слой (Fixed Core):** узлы и рёбра, которые создаёт и использует сам движок, —
  Source, Chunk, Document Registry, связь CONTAINS. Не зависят от предметной области.
- **Доменный слой:** типы узлов и рёбер объявляются в Domain Profile (`ontology.node_types`,
  `ontology.edge_types`), см. `docs/01` §2. При активации профиля Config Service автоматически
  создаёт в Neo4j constraints уникальности по `unique_key` каждого типа узла (`docs/01` §3).

Контекст: граф знания моделью хранит семантическую структуру; текст и эмбеддинги живут на оси
Chunk (см. §6).

## 2. Платформенные узлы

| Узел | Назначение | Ключевые свойства | Источник |
|------|------------|-------------------|----------|
| `Source` | Исходный документ (заголовок, URL, тип) | `source_url`, `domain`, `doc_type` | CONCEPT §4.1 (INGEST), docs/02 §1 |
| `Chunk` | Фрагмент текста (sliding window с overlap) | `chunk_id`, `chunk_index`, `chunk_size` (512), `overlap` (64), `text` | CONCEPT §4.1 (CHUNK), docs/02 §1 |
| `Document Registry` | Реестр документов и их версий | `doc_id`, `source_url`, `version`, `status` | CONCEPT §4.1 (COMMIT), docs/00 §1 |

## 3. Доменные узлы и их свойства

Из базового профиля `it` (Requirement → Concept → Contract) — `docs/01` §1, `docs/02` §2:

- **Requirement** — требование; уникальность по `unique_key` профиля (например, `requirement_id`).
- **Concept** — каноническая сущность; свойства: `canonical_name` (уникальное), `description`,
  `source_ids`, `extractor_version`.
- **Contract** — JSON-схема; иерархия склеивается на этапе CONTRACT по рёбрам EXTENDS и REFERENCES.

Профили `cinema` / `library` объявляют свои типы (Movie / Work, Director / Author и т.п.) аналогичным
образом — состав свойств задаётся профилем, общий набор свойств сохранён ниже.

**Общие свойства узлов графа** (глоссарий — `docs/glossary.md`, раздел Data Model):

| Свойство | Тип | Обязательное | Индекс | Описание |
|----------|-----|--------------|--------|----------|
| `canonical_name` | string | да (доменные сущности) | UNIQUE (constraint из профиля) | Каноническое имя после нормализации |
| `chunk_id` | string (uuid) | да (Chunk) | да | Идентификатор чанка; ключ связи с векторной осью |
| `source_ids` | list[string] | да | да | Документы-источники, из которых извлечена сущность |
| `extractor_version` | string | да | да | Версия промпта/словаря/модели экстракции |
| `description` | string | нет | — | Оригинальный текст до канонизации (сохраняется при трансформации `canonical_name`) |

## 4. Рёбра (типы связей)

Полный перечень доменных рёбер объявляется в `ontology.edge_types` профиля (направления `from → to`).
Документированные типы:

| Тип | Направление | Назначение | Источник |
|-----|-------------|------------|----------|
| `CONTAINS` | Chunk → Source | Принадлежность чанка документу | CONCEPT §4.1 (CHUNK) |
| `REQUIRES` | узел → узел | Зависимость (требование требует сущность/контракт) | docs/01 §1, expert_reviews.md |
| `REQUIRES_CONSTRAINT` | узел → узел | Зависимость с булевым условием | expert_reviews.md (семантическая модель) |
| `CONTRADICTS` | узел → узел | Логическое противоречие | CONCEPT §4 (VALIDATE), expert_reviews.md |
| `EXTENDS` | Contract → Contract | Наследование JSON-схем ($ref, allOf) | CONCEPT §4.1 (CONTRACT) |
| `REFERENCES` | узел → узел | Ссылка / вложенная схема | CONCEPT §4.1 (CONTRACT) |
| `SIMILAR_TO` | узел → узел (тип из профиля) | Семантическая близость (cosine ≥ 0.85); свойство `score` | docs/02 §3, CONCEPT §4.1 (DEDUP) |
| `ALTERNATIVE_TO` | узел → узел | Альтернативные варианты / решения | expert_reviews.md |
| `DEPRECATED_BY` | узел → узел | Устаревание версии контракта (версионирование) | expert_reviews.md |
| `IMPLEMENTS` | узел → узел | Реализация (контракт реализует требование и т.п.) | expert_reviews.md |
| `DEPENDS_ON` | узел → узел | Зависимость сущностей | expert_reviews.md |

Правила валидации на рёбрах (этап VALIDATE): `REQUIRES_CONSTRAINT` без цели → warning;
`CONTRADICTS` внутри одного требования → error; `REQUIRES` + `CONTRADICTS` одновременно → error;
`SIMILAR_TO` без обратной связи → auto-fix (CONCEPT §6.2 / docs/02 §4).

## 5. Индексы и constraints

- При активации профиля: `CREATE CONSTRAINT FOR (n:{node_type}) REQUIRE n.{unique_key} IS UNIQUE;`
  (автоматически, `docs/01` §3).
- Целевые индексы Neo4j (рекомендация экспертного ревью, «Важные пробелы»):
  `canonical_name`, `source_ids`, `extractor_version`.

## 6. Связь графовой и векторной осей (ADR-013)

Чанки текста хранятся в графовой оси как узлы `Chunk`; их векторные представления — в векторной
оси по ключу `chunk_id` (`upsert_vectors(chunk_id, embedding)`, размерность 1024 у bge-m3).
Поиск контекста: `VectorStoreProvider.vector_search` возвращает top-N чанков по `chunk_id`
(см. `docs/adapters_specification.md`, `docs/03_retriever.md` §1).

## 7. Версионирование и жизненный цикл

- Каждый узел несёт `extractor_version`; при смене промпта, словаря или модели запускается
  перерасчёт с новым `extractor_version` (версионирование графа).
- Обновление версий отражается в Document Registry (`status`), этап COMMIT — CONCEPT §4.1.
- Полный алгоритм удаления источников (`is_deleted`, `source_ids`), переиндексации и версионирования
  зафиксирован в `docs/05_adr_log.md` ADR-014; retention-политика — `docs/operations_requirements.md` §2.