# Кэширование и свежесть данных — карта изучения (методички)

Предметная область, которая всплыла при проработке Вехи 4-хвост (семантический
кэш в query-контуре, `docs/data_revision_analytics.md`). Часть распределённых
систем, но разбирается легче, если начинать с локального кэша.

**Как читать:** разделы ниже — от «обязательный минимум» к «углублённо».
В каждом — 1–2 конкретные книги/курсы/доки, чтобы не утонуть в интернете.

---

## 0. Зачем вообще кэш (сначала пойми проблему, потом решения)

- **«There are only two hard things in computer science: cache invalidation and
  naming things»** — Phil Karlton. Почитай, что стоит за цитатой.
- DDIA[^1], глава 12 (часть про кэши, **data-intensive** подход) — хорошая рамка:
  кэш — это торговля «латентность vs свежесть vs стоимость».
- Запиши в тетрадь: любые быстрые «двойные» слова — cache hit/miss, TTL, порог
  косинусной близости — это ответы на вопрос **«как часто можно ошибиться, и кто
  заплатит»**.

**Чек-пойнт «понял»:** объясни своими словами, почему `SEMANTIC_CACHE_TTL_S`
(дефолт 3600) — неправильный единственный ответ для данных, которые меняются
каждые 10 секунд.

---

## 1. Стратегии инвалидации кэша (ядро, обязательно)

- **Wikipedia: Cache invalidation** —
  https://en.wikipedia.org/wiki/Cache_invalidation
  — обзор 4 классических стратегий. Держи лист, где помечаешь: «наша — TTL +
  epoch-bump».
- **HTTP Caching RFC 9111** — https://datatracker.ietf.org/doc/html/rfc9111
  — чтобы увидеть, как «взрослые» умеют инвалидировать (validation, ETag,
  max-age). Аналогия с нашим `query:sc:<rev>:<domain>` прямая.

**Чек-пойнт:** что означают «explicit invalidation» vs «versioned keys» vs «TTL» —
и почему versioned keys лечат главную болезнь TTL (нельзя отменить «живую» запись).

---

## 2. Версионирование ключей / epoch-bump (та самая веха)

- Это прямого «стандартного учебника» нет — идея приходит из практики:
  - **Content-addressable storage** — https://en.wikipedia.org/wiki/Content-addressable_storage
  - **Merkle tree** — https://en.wikipedia.org/wiki/Merkle_tree
    (наш `sha256(активных content_hash)` — плоская мини-версия дерева Меркла).
- GitHub / git — самый наглядный пример: commit = идентификатор содержимого.
- Если захочешь копнуть глубже — «**Designing Data-Intensive Applications**»[^1]
  в части про меркл-деревья в репликации (глава про репликацию с «односторонней»
  сверкой).

**Чек-пойнт:** объясни, почему при переиндексации «того же контента» fingerprint
не должен меняться (идемпотентность, ADR-014), а счётчик — ломаться.

---

## 3. Локальный кэш → распределённый (без него не понять наш Redis)

Рекомендуемая связка по нарастающей:

1. **`cache-aside` (lazy loading) и `write-through`** — чтение и запись в кэш
   «мимо» источника. Найди в любом конспекте системного дизайна (например, в
   «System Design Interview» Алекс Сан, но достаточно и кратких статей).
2. **RFC 9111** (см. §1) — как HTTP-кэш решает то же.
3. **Redis как распределённый кэш** — официальная документация:
   - TTL — https://redis.io/docs/latest/commands/expire/
   - HASH структуры — https://redis.io/docs/latest/data-types/hashes/
   - почему наш `HGETALL` + linear scan по полю (в `RedisSemanticCache`) — «честная»
     реализация малого объёма; с ростом объёма — скорее индекс/Streams.

**Чек-пойнт:** нарисуй последовательность `hit/miss` и отметь, что попадает в ключ
хэша, что — в поле, что — в payload (это наша `query:sc:<rev>:<domain>`).

---

## 4. Свежесть данных и согласованность (выход на distributed)

- **Wikipedia: Consistency model** — https://en.wikipedia.org/wiki/Consistency_model
  — таблица моделей; найди «bounded staleness», «eventual consistency».
- **Wikipedia: Eventual consistency** — https://en.wikipedia.org/wiki/Eventual_consistency
  — пример из реального мира + почему «в конце концов» недостаточно.
- **DDIA[^1]**, глава 5 «Replication» (§ «Problems with replication lag») — лучший
  мост от «кэш» к «репликация», читать обязательно.
- Дальше идёт отдельная методичка — `distributed_consistency_learning.md`.

**Чек-пойнт:** назови, где в нашем прототипе «eventual» и где «bounded»,
и что меняет введение ревизии в инвариант L2-04.

---

## 5. Углубления (по желанию, после первого круга)

- Cache-Oblivious / B+-tree из «**Introduction to Algorithms**» (CLRS) — если
  интересна память.
- Real-time / streaming кэши — паттерн «time-to-refresh» из паттерн-каталога
  (например, у **Neo4j / TiKV** в доках про инвалидацию).
- Teoretика «двух генералов» и «CAP» — но это уже `distributed_consistency_learning.md`.

---

## Координация с проектом

Материалы §1–2 читать **перед** взятием тикетов Вехи 4-хвост; §4 — перед M4 (Eval,
где воспроизводимость среза = ревизия знаний). После прочтения — вноси правки в
`docs/data_revision_analytics.md`, если что-то стало яснее/глубже.

---

[^1]: **Designing Data-Intensive Applications** (Kleppmann, O'Reilly). Если купить —
  читать главы 5, 9, 12. HEAD — главы про replication lag и транзакции.