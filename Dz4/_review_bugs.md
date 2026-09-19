# Отчёт по багам из `/review` (evidence-first, без коммита)

Файл создаётся для фиксации находок из adversarial self-review. Каждая находка трактуется как
отдельная задача в бандле `concurrent-ingest-write-policy`, а не как «внутренняя заметка».

## [BUG] orchestrator.py:_write-exception-handler — компенсация без проверки is_transient

**Что:** в `orchestrator.py` обработчик `except BaseException` в блоке `_write` вызывает
`_compensate(graph, vector, stale_chunks, written_chunk_ids)` без проверки, что именно ошибка
была transient.

**Почему это баг:** transient-ошибка graph и transient-ошибка vector при best_effort-пути —
разные сценарии. Без проверки `vector.is_transient(exc)` компенсация может применяться, когда
transient была только на graph-оси (vector записался успешно), что может приводить к ложной
компенсации (удалению записанных vector-данных, которых не должно удалять).

**Статус:** исправлено — в обработчик добавлена проверка
`getattr(vector, \"is_transient\", lambda _: False)(exc)`; если не transient — просто `raise`,
без вызова `_compensate`.

**Файл задачи:** `openspec/changes/concurrent-ingest-write-policy/tasks.md`, §2.5.3 (переписана),
в т.ч.新增 2.5.3b: unit-тесты, что `_compensate` НЕ вызывается при non-transient ошибке vector.

---

## [RISK] tests/test_retry_compensation.py:TransientVector — is_transient не проверяет __cause__

**Что:** `TransientVector` в тестах реализует `is_transient` только через `isinstance(exc, TransientError)`.
В продакшене neo4j.py делает больше: разворачивает `__cause__` для transient-классификации
(UC12-02: обёртки transient должен пробрасывать источник через `__cause__`).

**Риск:** тесты могут пропускать сценарий, когда transient-classifier на границе адаптера
видит transient через `__cause__`, а не напрямую. В unit-тестах `is_transient` может быть
слишком простым.

**Статус:** частично закрыто задачей 2.5.4 (тесты `__cause__`-классификации). Но стоит добавить
в тесты проверку, что `is_transient` работает и с `__cause__`-обёртками, а не только с прямыми
исключениями.

**Файл задачи:** `tasks.md`, §2.5.4 (существует, но можно расширить формулировку), также
связано с `tasks.md` §2.1.2 (контракт `is_transient` на вендорах).

---

## [NIT] orchestrator.py: закомментированные/устаревшие формулировки N_RETRY_COMMIT

**Что:** в `orchestrator.py` (секция `_with_commit_retry`) некоторые комментарии говорят о
«N_RETRY_COMMIT — число попыток», но согласно spec.md это число **повторов** (всего попыток
N+1, `0` = одна попытка). Формулировки могут вводить в заблуждение новых читателей.

**Статус:** частично закрыто — в `proposal.md` ревизия уточнила формулировку. В `orchestrator.py`
лучше заменить комментарий; но это не блокирует сдачу, если формулировка в spec/tasks уже
согласована.

**Файл задачи:** `tasks.md`, §2.2.1 (существует, формулировка зафиксирована в spec).

---

## Примечание

Этот файл — только для фиксации. Исправления применяются в коде отдельно, тесты — отдельно,
коммит — только по команде пользователя после проверки.
