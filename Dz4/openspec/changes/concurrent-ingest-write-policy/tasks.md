# Задачи: политика конкурентной записи COMMIT

> Стадии внедрения по `proposal.md`: S1 (сейчас, обход для прогона M5) → S2 (контракт+реализация,
> M5-хвост) → S3 (очередь ingestion, стадия «Рост»).

## S1. Быстрый обход (для полного прогона M5)

- [x] 1.1. `infra/compose.eval.yaml`: `INGEST_MAX_CONCURRENT=1` — пометка «S1-обход (deadlock),
      снять после S2». Проверено `docker inspect ohw-ingestion`.
- [ ] 1.2. `docs/06_operations_and_risks.md` §5: добавить риск «Neo4j deadlock при
      INGEST_MAX_CONCURRENT>1» с митигацией (S1: сериализация; S2: retry+sort).
- [ ] 1.3. Полный прогон M5 (вариант 1 и 2) проходит на концентрации 1 (фиксация результата
      в lift_report + история).

## S2. Контракт и реализация (M5-хвост)

### 2.1. Контракт transient-классификации

- [x] 2.1.1. `retrieval/adapters/base.py`: `transient_aware() -> bool` (дефолт False) и
      `is_transient(exc) -> bool` на `GraphStoreProvider`/`VectorStoreProvider`;
      docstring-контракт (ядро не импортирует вендорские пакеты, L1-02).
      Выполнено в `c21236e`: `base.py` — дефолты обоих провайдеров.
- [x] 2.1.2. `neo4j.py`: `Neo4jGraphStore`/`Neo4jVectorStore` — `transient_aware()->True`,
      `is_transient()` по `neo4j.exceptions.{TransientError, ServiceUnavailable}`;
      `inmemory.py` — дефолт ABC.
      Выполнено в `c21236e`: `neo4j.py:_is_transient_exc` (защита от циклов),
      оба класса; InMemory — дефолты ABC.

### 2.2. Retry-loop в CommitStage

> Ревизия 2026-09-17 по ревью №12 (UC12-02/03/06): уточнены задачи.

- [x] 2.2.1. `orchestrator.py`: `_with_commit_retry(stores, fn)` — `N_RETRY_COMMIT`
      повторов после первой попытки (всего N+1; `0` — ровно одна попытка),
      backoff `0.2*2^i + jitter(0.1)`, transient-классификация через декларации
      провайдеров; env-параметры валидируются (отрицательные/нечисловые — fail-fast).
      Выполнено в `c21236e`: `_with_commit_retry` + дефолты из env; этот проход —
      fail-fast-валидация `_parse_retry_env` (нечисловые/отрицательные/пустые —
      `ValueError`, `N_RETRY_COMMIT=0` валиден), guard `attempts < 1` в
      `_with_commit_retry`; тесты ниже в 2.5.1 (подпункт env).
- [x] 2.2.2. Обернуть `_write_atomic`, `_write_best_effort`, тело `soft_delete_source`;
      компенсация best_effort — только после исчерпания повторов (UC12-02: путь
      «транзиент сбой второй оси» не должен компенсироваться до retry).
      Выполнено в `c21236e`: обе оси обёрнуты, `_compensate` — после исчерпания.
- [x] 2.2.3. Логирование transient-повторов с номером попытки (наблюдаемость, L4-04 паттерн).
      Выполнено в `c21236e`: `_log.warning` внутри `_with_commit_retry`.
- [x] 2.2.4. Сохранение причины ошибки (UC12-02): `raise ... from exc` во всех обёртках;
      `is_transient` neo4j-адаптера проверяет `exc` и его `__cause__`.
      Выполнено: `neo4j.py:_is_transient_exc` разворачивает цепочку `__cause__`
      (с защитой от циклов); документирован контракт в `base.py`. Тесты 2.5.4
      зелёные, ruff/mypy чисто.
- [x] 2.2.5. Согласованность soft-delete (UC12-06): `registry.rollback_soft_delete`
      (новый метод реестра) при окончательном отказе удаления; повторный вызов
      `soft_delete_source` после частичного удаления — безопасен.
      Выполнено: `registry.py:rollback_soft_delete` (последняя deleted-версия →
      active, no-op иначе); `soft_delete_source` откатывает реестр на `BaseException`
      (тот же контур компенсации, что и best-effort, review-13); тесты 2.5.5 зелёные.

### 2.3. Детерминированный порядок записи

- [x] 2.3.1. `CommitStage._write`: `nodes.sort(node_id)`, `edges.sort(from_id, to_id, type)`.
      Выполнено в `c21236e`: `orchestrator.py:_write`.
- [x] 2.3.2. `_upsert_nodes`/`_upsert_edges` (neo4j.py) — контрактная сортировка входящего
      списка (двойная страховка). Выполнено в `c21236e`.

### 2.4. Инварианты, ADR, доки

- [x] 2.4.1. `docs/invariants.md`: новый инвариант L3-06 (черновик) «конкурентная запись
      COMMIT: детерминированный порядок + retry transient до N_RETRY_COMMIT»
      (формулировка по UC12-07: порядок снижает вероятность deadlock, не исключает —
      без заявления «deadlock невозможен»).
      Выполнено: `docs/invariants.md` v10 — L3-06 с формулировкой «снижает вероятность,
      не исключает», обоснование ADR-028/UC12-07, ссылка на спеку §2/§3.
- [x] 2.4.2. `docs/05_adr_log.md`: ADR-028 (черновик) — политика конкурентной записи,
      границы (не-transient не ретраим, без 2PC); семантика `N_RETRY_COMMIT` = число
      повторов; soft-delete-согласованность (§2а спеки).
      Выполнено: ADR-028 (Статус: Draft) после ADR-027 — границы (без 2PC, без
      single-writer на S2), семантика N_RETRY_COMMIT, UC12-02/06/07, затрагиваемые файлы.
- [x] 2.4.3. `docs/06` §5 (риск из S1) дополняется ссылкой на S2-решение; ADR-028 в связях.
      Выполнено: `docs/06_operations_and_risks.md` §5 — блок «Риск №7 (S1-обход, закрыт
      S2 — ADR-028)»: INGEST_MAX_CONCURRENT, retry + сортировка + rollback_soft_delete,
      возврат дефолта 2; ссылка на L3-06 и стадию «Рост».
- [x] 2.4.4. `docs/history.md`: Этап 13 — фиксация deadlock-находки и закрытия S2.
      Выполнено: «Этап 14: Политика конкурентной записи COMMIT» (Этап 13 уже занят
      M4 eval) — находка M5, S1-обход, S2-реализация, верификация (378 passed, ruff,
      mypy), следующие шаги; баннер v14 и связь ADR-001–ADR-028 обновлены.

### 2.5. Тесты

- [x] 2.5.1. Unit: фейк-store бросает transient N раз → retry, на N+1 — успех
      (verify вызовы = N повторов); не-transient — failed без повторов.
      Выполнено в `c21236e` (`test_commit_stage.py` + `test_retry_compensation.py`).
      Подпункт env добит: `_parse_retry_env` — дефолты `{}`→(3, 0.2, 0.1),
      `N_RETRY_COMMIT=0` валиден (ровно одна попытка), отрицательные/нечисловые/
      пустые значения → `ValueError` (fail-fast), `attempts=0` → `ValueError`.
- [x] 2.5.2. Unit: порядок вызовов `upsert_nodes`/`upsert_edges` отсортирован
      по `node_id` и `(from_id, to_id, type)` (mock-провайдер, событийная запись).
      Выполнено в `c21236e`: `test_commit_plan_nodes_and_edges_sorted`.
- [x] 2.5.3. Unit: компенсация best_effort выполняется только после исчерпания retry
      (UC12-02: transient второй оси на 1-й попытке → повтор → успех без компенсации;
      исчерпание → компенсация; non-transient → немедленный отказ; сбой самой
      компенсации не маскирует исходную причину). Также: transient-сбой ПЕРВОЙ оси
      (граф) → повтор до исчерпания, ошибки поднимаются как есть (не оборачиваются
      в CommitStageError), векторная ось не затрагивается, компенсации нет —
      тест `test_best_effort_first_axis_transient_fails_without_compensation`.
      Решение (review-13): перехват `BaseException` в `_write_best_effort` —
      НАМЕРЕННЫЙ: прерывание (Ctrl+C) тоже приводит к компенсации, чтобы оси
      не остались рассинхронизированными (L2-03).
      Выполнено в `c21236e` (`test_retry_compensation.py`).
- [x] 2.5.4. Unit (UC12-02): `__cause__` обёрнутой ошибки классифицируется как
      transient на границе адаптера. Выполнено в `c21236e` (`test_transient_classification.py`).
- [x] 2.5.5. Unit (UC12-06): окончательный отказ удаления → `rollback_soft_delete`
      вызван, реестр снова активен; повторный `soft_delete_source` проходит полный путь.
      Выполнено (`test_commit_stage.py`): roundtrip soft_delete→rollback_soft_delete,
      отказ хранилища → реестр active + данные на месте + повторный полный путь,
      частичное удаление при сбое → откат + идемпотентный повтор.
- [x] 2.5.6. `uv run pytest -q` зелёно; `uv run ruff check .`, `uv run mypy src` чисто.
      Выполнено в dev-образе: pytest 378 passed (2 deselected e2e), ruff src+tests
      чисто (в gitignored `prototype/reports/*` — 4 предсуществующих замечания на
      артефактах, не входящих в репозиторий), mypy — без замечаний.
- [ ] 2.5.7. Интеграция: контрактные тесты Neo4j-адаптера — transient-классификация
      на реальном драйвере (если доступен стек).

### 2.6. Приёмка

- [ ] 2.6.1. Live-стек Neo4j: полный прогон M5 с `INGEST_MAX_CONCURRENT=2` без deadlock;
      повторная загрузка — no-op (L2-06).
- [ ] 2.6.2. Ретракт дефолта: `compose.eval.yaml` возвращается к 2 (после успешного прогона),
      комментарий S1 снимается.

## S3. Очередь ingestion (стадия «Рост», `docs/06` §4)

> Реализуется отдельным бандлом; здесь — только проектные якоря.

- [ ] 3.1. Очередь ingestion (Redis/RabbitMQ → Kafka), N воркеров — правила записи из S2
      применяются к воркеру как единице.
- [ ] 3.2. Решение по single-writer пути (по потребности) — отдельный ADR.
- [ ] 3.3. Инвариант L3-06 не меняется; триггеры масштабирования — `docs/06` §4 (T1/T5).