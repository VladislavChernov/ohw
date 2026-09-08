"""Task Queue (ADR-023): TaskStore, InMemoryTaskQueue, сериализация задач/событий."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from graphrag_proto.query_service.models import Task, new_task_id
from graphrag_proto.query_service.store import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    TERMINAL,
    TaskStore,
)
from graphrag_proto.query_service.task_queue import (
    InMemoryTaskQueue,
    _event_from_fields,
    _task_from_fields,
    envelope,
)


def test_new_task_id_format() -> None:
    assert new_task_id().startswith("q_")
    assert len(new_task_id()) == len("q_") + 8


def test_store_lifecycle(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "query_tasks.sqlite")
    task_id = new_task_id()
    assert store.get(task_id) is None

    store.create(task_id, "it", "вопрос")
    row = store.get(task_id)
    assert row is not None and row["status"] == STATUS_QUEUED and row["domain"] == "it"

    store.mark_running(task_id)
    assert store.get(task_id)["status"] == STATUS_RUNNING

    store.mark_succeeded(task_id)
    assert store.get(task_id)["status"] == STATUS_SUCCEEDED

    failed_id = new_task_id()
    store.create(failed_id, "it", "вопрос")
    store.mark_running(failed_id)
    store.mark_failed(failed_id, "boom")
    assert store.get(failed_id)["status"] == STATUS_FAILED
    assert store.get(failed_id)["error"] == "boom"

    cancelled_id = new_task_id()
    store.create(cancelled_id, "it", "вопрос")
    store.mark_cancelled(cancelled_id)
    assert store.get(cancelled_id)["status"] == STATUS_CANCELLED
    store.close()


def test_terminal_status_not_overwritten(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "query_tasks.sqlite")
    store.create("q_t1", "it", "вопрос")
    store.mark_running("q_t1")
    store.mark_succeeded("q_t1")

    assert store.mark_cancelled("q_t1") is False
    assert store.mark_failed("q_t1", "поздний сбой") is False
    assert store.mark_running("q_t1") is False
    assert store.get("q_t1")["status"] == STATUS_SUCCEEDED
    assert store.get("q_t1")["error"] is None
    store.close()


def test_task_carries_redis_entry_id() -> None:
    task = Task(task_id="q_1", domain="it", query="a", entry_id="1720000000000-0")
    assert task.entry_id == "1720000000000-0"
    assert asdict(task)  # entry_id не ломает сериализацию (dataclass)


def test_terminal_statuses() -> None:
    assert set(TERMINAL) == {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED}


def test_envelope_shape() -> None:
    env = envelope("status", "q_1", {"stage": "llm"})
    assert env["type"] == "status"
    assert env["task_id"] == "q_1"
    assert env["payload"] == {"stage": "llm"}
    assert isinstance(env["ts"], str) and env["ts"]


def test_inmemory_queue_fifo_claim() -> None:
    queue = InMemoryTaskQueue()
    queue.submit(Task(task_id="q_1", domain="it", query="a"))
    queue.submit(Task(task_id="q_2", domain="it", query="b"))
    assert queue.claim("w1").task_id == "q_1"
    assert queue.claim("w1").task_id == "q_2"
    assert queue.claim("w1") is None


def test_inmemory_queue_cancel_skips_claim_and_sets_flag() -> None:
    queue = InMemoryTaskQueue()
    queue.submit(Task(task_id="q_1", domain="it", query="a"))
    queue.cancel("q_1")
    assert queue.is_cancelled("q_1") is True
    assert queue.claim("w1") is None


def test_inmemory_events_replay_until_terminal() -> None:
    queue = InMemoryTaskQueue()
    queue.publish("q_1", "status", {"stage": "embedding"})
    queue.publish("q_1", "token", {"delta": "отв"})
    queue.publish("q_1", "done", {"text": "ответ"})

    collected = [(e.type, e.payload) for e in queue.events("q_1")]
    assert collected == [
        ("status", {"stage": "embedding"}),
        ("token", {"delta": "отв"}),
        ("done", {"text": "ответ"}),
    ]


def test_inmemory_events_terminal_cancelled() -> None:
    queue = InMemoryTaskQueue()
    queue.publish("q_1", "status", {"stage": "running"})
    queue.cancel("q_1")
    types = [e.type for e in queue.events("q_1")]
    assert types == ["status", "status"]


def test_serialization_roundtrip() -> None:
    task = Task(task_id="q_7", domain="it", query="вопрос", metadata={"domain": "it", "depth": 3})
    fields = {
        b"task_id": task.task_id.encode(),
        b"domain": task.domain.encode(),
        b"query": task.query.encode("utf-8"),
        b"metadata": __import__("json").dumps(task.metadata, ensure_ascii=False).encode("utf-8"),
    }
    restored = _task_from_fields(fields)
    assert restored == task

    event = envelope("status", "q_7", {"stage": "llm"})
    efields = {k.encode(): v.encode("utf-8") for k, v in event.items() if isinstance(v, str)}
    efields[b"payload"] = __import__("json").dumps(event["payload"], ensure_ascii=False).encode("utf-8")
    ev = _event_from_fields(efields)
    assert ev.type == "status" and ev.task_id == "q_7" and ev.payload == {"stage": "llm"}