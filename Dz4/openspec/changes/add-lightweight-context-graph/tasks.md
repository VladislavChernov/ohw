# Задачи: primitive ingest и sparse context graph

> Стадия: corrective change после typed-ontology experiment. Сначала обновляется нормативная
> документация, затем добавляются regression-тесты, после этого меняется код. Live-приёмка и
> полная миграция существующей Neo4j базы не входят в DoD.

## 1. Документация и контракты

- [x] 1.1. Обновить `CONCEPT.md`, `docs/01`, `docs/02`, `docs/03`, `docs/data_model.md`,
      `docs/glossary.md`, `docs/adapters_specification.md`, `docs/invariants.md` и
      `docs/prototype_requirements.md`: убрать mandatory typed ontology, DDL и parallel
      graph-first runtime; описать primitive ingest, optional enrichment и vector-first expansion.
- [x] 1.2. Добавить ADR о lightweight context graph и пометить typed-ontology claims из
      `eval-graph-contribution-experiment` как superseded, не удаляя историю.
- [x] 1.3. Обновить eval-документацию: baseline vector-only, target vector + bounded graph
      expansion/boost; manifest/QA trace фиксируют seeds, paths, depth, boost и fallback.
- [x] 1.4. Разобраны vector-first examples и exercises, отработан
      справочники heavy graph и parallel-axis, явные Target/Runtime/Planned статусы.
- [x] 1.5. Зафиксировать два lifecycle графа: inline enrichment после vector commit и отдельный
      offline enrichment/rebuild; readiness/revision не должны менять vector-only baseline.

## 2. Primitive ingest и optional enrichment

> Baseline-контур не зависит от graph. Задачи graph experiment выполняются отдельно и могут
> быть отложены без блокировки vector-only MVP.

- [x] 2.1. Сделать profile/AI/graph enrichment optional для document ingest; ingest без
      profile, LLM, tags и links завершается успешно.
- [x] 2.2. Добавить optional `tags`/`links`/metadata в ingestion contract; сохранять `origin`,
      `confidence`, `source_ids` и не смешивать manual/AI данные.
- [x] 2.3. Ввести `tag_id`/alias resolution в пределах domain; multilingual aliases могут
      ссылаться на один tag, неоднозначные кандидаты не объединяются молча.
- [ ] 2.4. Добавить идемпотентный offline enrichment/rebuild job: построение graph projection
      и backfill vector metadata без повторного изменения document content.

## 3. Sparse graph adapter (experiment)

> Это optional experiment, а не prerequisite для baseline. Adapter может отсутствовать или быть
> не готов; vector-only fallback остаётся валидным.

- [x] 3.1. Убрать `_validate_ontology` и `ensure_schema` из runtime ingest; удалить typed
      edge whitelist и обязательные `Requirement|Concept|Contract` labels.
- [x] 3.2. Сохранить generic graph upsert/transaction/retry/compensation; добавить generic
      `expand(context_ids, direction, max_depth, max_fanout, max_nodes)` для адаптеров.
- [x] 3.3. Сохранить `Source`/`Chunk` anchors, `MENTIONS` и provenance как технические связи;
      custom properties/kinds не теряются InMemory и Neo4j adapters.
- [ ] 3.4. Фиксировать readiness и projection revision offline job; stale graph не должен
      silently считаться graph-enabled.

## 4. Vector-first graph experiment

- [x] 4.1. Vector metadata сохраняет `context_ids`/`tag_ids` и custom enrichment metadata.
- [x] 4.2. experiment pipeline выполняет vector search → bounded graph expansion → boost; baseline
      не вызывает graph и не меняет vector ranking.
- [x] 4.3. Graph failure даёт vector-only fallback с degraded marker; target eval не считает
      silent fallback успешным graph result.
- [x] 4.4. Context Assembly включает bounded graph evidence, provenance и budget; expansion
      не вытесняет vector body бесконтрольно.

## 5. Тесты и проверка

- [x] 5.1. Regression: ingest без profile/AI/ontology и optional tags/links.
- [x] 5.2. Regression: multilingual alias/tag identity и dynamic custom properties/edge kinds.
- [ ] 5.3. Regression: vector seeds graph expansion, bounded budget, boost, fallback и
      adapter parity.
- [x] 5.4. Eval regression: baseline/target parity, seed/path/boost trace и contribution metrics.
- [x] 5.5. `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src` в persistent
      dev-контейнере с host `RUN_CODE_COMMIT`; live Neo4j/eval остаются отдельными задачами.

> 2.4 и 3.4 остаются Planned: offline replay/backfill и projection readiness/revision ещё
> требуют отдельного change. 5.3 требует live Neo4j parity check.
