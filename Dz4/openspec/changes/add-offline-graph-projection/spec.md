# Спека: Offline graph projection lifecycle

## Scenario: projection state before first build

**WHEN** domain содержит active documents, но offline graph projection ещё не запускалась

**THEN** state для domain отсутствует или имеет status `pending`; vector-only ingest и retrieval продолжают работать; graph experiment не выдаётся за enabled.

## Scenario: idempotent offline rebuild

**WHEN** operator запускает rebuild для domain с заданными `data_revision` и `config_fingerprint`

**THEN** job создаёт или обновляет generic graph projection, не меняя document content, и после успешной verification записывает `projection_revision` со status `ready`.

## Scenario: repeated job key

**WHEN** job запускается повторно с тем же `domain`, `data_revision`, `projection_revision` и `config_fingerprint`

**THEN** job завершается безопасно, не создаёт duplicate nodes, edges или provenance entries; результат совпадает с предыдущей успешной projection.

## Scenario: vector metadata backfill

**WHEN** projection job связывает chunks с context nodes

**THEN** backfill добавляет `context_ids`, `tag_ids` и projection metadata в vector records, сохраняя text, embedding, source/chunk provenance и content hash; ingest document при этом не создаёт новую content revision.

## Scenario: partial graph failure

**WHEN** graph batch падает после обработки части источников

**THEN** state не становится `ready`; job фиксирует `degraded` или `failed`, retry безопасен, а vector baseline и уже подтверждённые vector records остаются доступными.

## Scenario: stale projection

**WHEN** active data revision новее сохранённой `data_revision` projection

**THEN** state помечается `stale`, graph experiment не вызывается, retrieval возвращает vector-only fallback с `graph_degraded=true` и причиной `stale`.

## Scenario: failed or missing projection

**WHEN** state имеет status `failed`, `pending`, `degraded` или отсутствует

**THEN** graph experiment блокируется, fallback не кэшируется как успешный graph result, а eval/trace помечают run как degraded или not measured.

## Scenario: domain isolation

**WHEN** rebuild запускается для domain `library`

**THEN** state, nodes, edges, metadata backfill и retries не изменяют projection state или vector metadata domain `it`; готовность проверяется по тому же domain-scoped revision.

## Scenario: manual provenance during rebuild

**WHEN** offline rebuild обрабатывает manual tags/links и AI suggestions с одинаковыми `tag_id`

**THEN** сохраняются manual origin, canonical values и properties; AI добавляет provenance/confidence, но не перезаписывает manual data.

## Scenario: source retention

**WHEN** документ soft-deleted или superseded

**THEN** rebuild удаляет его chunk anchors и provenance из актуальной projection/backfill, не удаляя evidence других источников; inactive evidence не проходит expansion.

## Scenario: retry and lease

**WHEN** job claim истёк или worker завершился частично

**THEN** следующий claimant проверяет job key и state, не создаёт конкурирующую запись и может безопасно повторить незавершённые batch operations.

## Scenario: observability

**WHEN** job выполняется, succeeds, retries, becomes stale или fails

**THEN** audit record и метрики содержат domain, job id, input/projection revisions, status, duration, counts и last error; secrets и payload documents не логируются.

## Scenario: baseline independence

**WHEN** graph state store, graph adapter и projection job недоступны

**THEN** primitive ingest, vector metadata и vector-only retrieval продолжают работать; ошибка graph не превращает документ в failed ingest.
