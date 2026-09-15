"""Task Queue (ADR-023): TaskStore, InMemoryTaskQueue, сериализация задач/событий."""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
    RedisStreamTaskQueue,
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


def test_inmemory_queue_depth() -> None:
    queue = InMemoryTaskQueue()
    assert queue.depth() == 0
    queue.submit(Task(task_id="q_1", domain="it", query="a"))
    queue.submit(Task(task_id="q_2", domain="it", query="b"))
    assert queue.depth() == 2
    queue.claim("w1")
    assert queue.depth() == 2  # pending 1 (q_2) + inflight 1 (q_1)
    queue.ack("q_1")
    assert queue.depth() == 1


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


def test_inmemory_events_heartbeat_keepalive() -> None:
    queue = InMemoryTaskQueue()
    stream = queue.events("q_1", heartbeat_interval_s=0.05)
    assert next(stream).type == "heartbeat"
    queue.publish("q_1", "status", {"stage": "running"})
    assert next(stream).type == "status"
    queue.publish("q_1", "done", {"text": "ok"})
    assert next(stream).type == "done"


def test_inmemory_reclaim_returns_stale_claimed_task() -> None:
    queue = InMemoryTaskQueue()
    queue.submit(Task(task_id="q_1", domain="it", query="a"))
    assert queue.claim("w1") is not None
    time.sleep(0.05)
    queue.reclaim("w1", min_idle_s=0.01)
    claimed = queue.claim("w1")
    assert claimed is not None and claimed.task_id == "q_1"


def test_inmemory_reclaim_skips_cancelled_and_fresh() -> None:
    queue = InMemoryTaskQueue()
    queue.submit(Task(task_id="q_fresh", domain="it", query="a"))
    queue.claim("w1")  # q_fresh — не простывший
    queue.submit(Task(task_id="q_old_cancel", domain="it", query="b"))
    queue.claim("w1")  # q_old_cancel — в работе, затем отменена
    queue.cancel("q_old_cancel")
    time.sleep(0.05)
    queue.reclaim("w1", min_idle_s=0.5)  # 0.05с < 0.5с: q_fresh ещё «свежая»
    assert queue.claim("w1") is None


class _FakeRedisClient:
    def __init__(self, entries: list[tuple[str, dict[bytes, bytes]]]) -> None:
        self._entries = entries
        self.acks: list[str] = []
        self.xadded: list[dict[str, Any]] = []
        self.xautoclaim_kwargs: dict[str, Any] = {}

    def xautoclaim(self, **kwargs: Any) -> list[Any]:
        self.xautoclaim_kwargs = dict(kwargs)
        return ["query:tasks", self._entries, "0-0"]

    def xack(self, stream: str, group: str, entry_id: str) -> None:
        self.acks.append(entry_id)

    def xadd(self, stream: str, fields: dict[str, Any]) -> None:
        self.xadded.append(fields)


def test_redis_reclaim_requeues_orphaned_pel() -> None:
    queue = RedisStreamTaskQueue(redis_url="redis://test:6379/0")
    queue._client = _FakeRedisClient(
        [("1720000000000-0", {b"task_id": b"q_1", b"domain": b"it", b"query": "вопрос".encode(), b"metadata": b"{}"})]
    )
    queue.reclaim("worker-1", min_idle_s=60.0)
    assert queue._client.xautoclaim_kwargs["min_idle_time"] == 60000
    assert queue._client.xautoclaim_kwargs["consumername"] == "worker-1"
    assert queue._client.acks == ["1720000000000-0"]
    assert len(queue._client.xadded) == 1
    assert queue._client.xadded[0]["task_id"] == "q_1"


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