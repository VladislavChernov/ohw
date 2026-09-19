"""Транзиент-классификация Neo4j-адаптера с развёрткой __cause__ (UC12-02, ADR-028).

Автономные unit-тесты: реальный пакет `neo4j` установлен как runtime-зависимость,
драйвер/сеть не нужны — только исключения.
"""

from __future__ import annotations

import pytest
from neo4j.exceptions import DatabaseError, TransientError

from graphrag_proto.ingestion_service.pipeline.orchestrator import CommitStageError
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore

_URI = "bolt://localhost:7999"  # соединение не открывается: тестируем только классификацию


def _store() -> Neo4jGraphStore:
    return Neo4jGraphStore(uri=_URI, user="neo4j", password="unused")


def test_direct_transient() -> None:
    assert _store().is_transient(TransientError("deadlock detected"))


def test_wrapped_runtimeerror_cause() -> None:
    """Обёртка адаптера `RuntimeError(... from exc)` классифицируется по причине."""
    cause = TransientError("Neo.TransientError.Transaction.DeadlockDetected")
    wrapped = RuntimeError(f"COMMIT vector axis failed: {cause}")
    wrapped.__cause__ = cause
    assert _store().is_transient(wrapped)


def test_double_wrapped_commit_stage_error_cause() -> None:
    """Путь CommitStage: CommitStageError -> RuntimeError -> TransientError."""
    cause = TransientError("deadlock")
    mid = RuntimeError(f"COMMIT vector axis failed: {cause}")
    mid.__cause__ = cause
    top = CommitStageError("commit failed", compensated=True)
    top.__cause__ = mid
    assert _store().is_transient(top)


def test_deep_chain_and_non_transient_leaf() -> None:
    """Глубокая цепочка причин доходит до transient-листа; non-transient лист — False."""
    cause = TransientError("service unavailable")
    deep: BaseException = cause
    for i in range(10):
        nxt = RuntimeError(f"layer {i}: {deep}")
        nxt.__cause__ = deep
        deep = nxt
    assert _store().is_transient(deep)

    non_transient = ValueError("bad payload")
    non_transient.__cause__ = DatabaseError("constraint violation")
    assert not _store().is_transient(non_transient)


def test_cause_cycle_is_safe() -> None:
    """Циклический `__cause__` не приводит к бесконечному циклу."""
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert not _store().is_transient(a)


def test_non_base_exception_rejected() -> None:
    with pytest.raises(AttributeError):
        _store().is_transient("not an exception")  # type: ignore[arg-type]
