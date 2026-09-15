"""Task Queue прототипа (ADR-023): два контрактно-эквивалентных транспорта.

- `RedisStreamTaskQueue` (env QUERY_QUEUE=redis): Valkey, stream `query:tasks`
  (XADD/XREADGROUP/XACK, consumer-group `query-workers`) + per-task stream событий
  `query:events:{task_id}` (XADD воркером / XRANGE+XREAD подписчиком SSE).
- `InMemoryTaskQueue` (env QUERY_QUEUE=inmemory): очередь + история событий в памяти;
  тот же контракт — для тестов и демо без Valkey.

Соглашения: task_id `q_<hex8>`; терминальные события — `done`, `error`,
`status` со стадией `cancelled` (конверт ADR-016).
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from graphrag_proto.query_service.models import Event, Task

STREAM_TASKS = "query:tasks"
GROUP_WORKERS = "query-workers"
EVENT_PREFIX = "query:events:"
CANCEL_PREFIX = "query:cancel:"

TERMINAL_TYPES = ("done", "error")


def envelope(event_type: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": event_type,
        "task_id": task_id,
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "payload": payload or {},
    }


def _is_terminal(event: Event) -> bool:
    if event.type in TERMINAL_TYPES:
        return True
    return bool(event.type == "status" and (event.payload or {}).get("stage") == "cancelled")


class TaskQueue(ABC):
    @abstractmethod
    def submit(self, task: Task) -> None:
        """Публикация задачи в очередь."""

    @abstractmethod
    def claim(self, worker_id: str) -> Task | None:
        """Забор задачи воркером (блокирующий на короткий интервал; None — пусто)."""

    @abstractmethod
    def ack(self, task_id: str, entry_id: str | None = None) -> None:
        """Подтверждение успешной обработки."""

    @abstractmethod
    def fail(self, task_id: str, error: str, entry_id: str | None = None) -> None:
        """Отметка сбоя (ack с флагом fail; статус пишет TaskStore)."""

    @abstractmethod
    def publish(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Событие задачи (конверт ADR-016) — для подписчика SSE."""

    @abstractmethod
    def events(self, task_id: str, heartbeat_interval_s: float = 15.0) -> Iterator[Event]:
        """История + новые события задачи (до терминального события).

        При простое дольше `heartbeat_interval_s` — контрольное событие
        `type="heartbeat"` (keep-alive SSE-подписки, детект обрыва клиента).
        """

    @abstractmethod
    def is_cancelled(self, task_id: str) -> bool:
        """Флаг отмены (DELETE /query/tasks/{id})."""

    @abstractmethod
    def cancel(self, task_id: str) -> None:
        """Отмена: флаг + событие status: cancelled."""

    @abstractmethod
    def reclaim(self, worker_id: str, min_idle_s: float = 60.0) -> None:
        """Возврат к забору задач, зависших после claim без ack дольше `min_idle_s`.

        Задача не должна теряться при падении/транспортном сбое воркера (PEL-хвосты
        Redis Streams, in-flight без ack в памяти): reclaim возвращает её в очередь,
        и её обработку забирает следующий claim.
        """

    def depth(self) -> int:
        """Очередь необработанных задач (pending + inflight); 0 = пусто.

        Для `RedisStreamTaskQueue`: XLEN основного стрима (все записи, не только
        необработанные) — прототипный upper bound; детальный расчёт (XLEN - acked)
        требует отдельного счётчика.
        """
        return 0


class _MemoryChannel:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.cond = threading.Condition()
        self.terminal = False


class InMemoryTaskQueue(TaskQueue):
    def __init__(self) -> None:
        self._queue: list[Task] = []
        self._cond = threading.Condition()
        self._channels: dict[str, _MemoryChannel] = {}
        self._cancelled: set[str] = set()
        self._inflight: dict[str, tuple[Task, float]] = {}

    def depth(self) -> int:
        """Потребленные (claim) + ожидающие в очереди задачи."""
        return len(self._queue) + len(self._inflight)

    def _channel(self, task_id: str) -> _MemoryChannel:
        with self._cond:
            ch = self._channels.get(task_id)
            if ch is None:
                ch = _MemoryChannel()
                self._channels[task_id] = ch
            return ch

    def submit(self, task: Task) -> None:
        with self._cond:
            self._queue.append(task)
            self._cond.notify_all()

    def claim(self, worker_id: str, timeout_s: float = 1.0) -> Task | None:
        deadline = time.monotonic() + timeout_s
        with self._cond:
            while self._queue:
                if time.monotonic() >= deadline:
                    return None
                task = self._queue.pop(0)
                if task.task_id in self._cancelled:
                    continue
                self._inflight[task.task_id] = (task, time.monotonic())
                return task
            self._cond.wait(timeout=timeout_s)
            while self._queue:
                task = self._queue.pop(0)
                if task.task_id not in self._cancelled:
                    self._inflight[task.task_id] = (task, time.monotonic())
                    return task
            return None

    def ack(self, task_id: str, entry_id: str | None = None) -> None:
        with self._cond:
            self._inflight.pop(task_id, None)

    def fail(self, task_id: str, error: str, entry_id: str | None = None) -> None:
        with self._cond:
            self._inflight.pop(task_id, None)

    def reclaim(self, worker_id: str, min_idle_s: float = 60.0) -> None:
        with self._cond:
            now = time.monotonic()
            stale = [task for task, claimed_at in self._inflight.values() if now - claimed_at >= min_idle_s]
            for task in stale:
                self._inflight.pop(task.task_id, None)
                if task.task_id not in self._cancelled:
                    self._queue.append(task)
            if stale:
                self._cond.notify_all()

    def publish(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = Event(
            type=event_type,
            task_id=task_id,
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            payload=payload or {},
        )
        ch = self._channel(task_id)
        with ch.cond:
            ch.events.append(event)
            if _is_terminal(event):
                ch.terminal = True
            ch.cond.notify_all()

    def events(self, task_id: str, heartbeat_interval_s: float = 15.0) -> Iterator[Event]:
        ch = self._channel(task_id)
        index = 0
        last_heartbeat = time.monotonic()
        while True:
            with ch.cond:
                now = time.monotonic()
                while index >= len(ch.events):
                    if ch.terminal:
                        return
                    if now - last_heartbeat >= heartbeat_interval_s:
                        break
                    delay = last_heartbeat + heartbeat_interval_s - now
                    ch.cond.wait(timeout=min(delay, 1.0))
                    now = time.monotonic()
                if index >= len(ch.events):
                    if ch.terminal:
                        return
                    last_heartbeat = now
                    yield_event = Event(
                        type="heartbeat",
                        task_id=task_id,
                        ts=datetime.now(UTC).isoformat(timespec="seconds"),
                        payload={},
                    )
                else:
                    yield_event = ch.events[index]
                    index += 1
            yield yield_event

    def is_cancelled(self, task_id: str) -> bool:
        with self._cond:
            return task_id in self._cancelled

    def cancel(self, task_id: str) -> None:
        with self._cond:
            self._cancelled.add(task_id)
        self.publish(task_id, "status", {"stage": "cancelled"})


class RedisStreamTaskQueue(TaskQueue):
    """Valkey/Redis Streams (ADR-023). `redis` импортируется лениво."""

    def __init__(
        self,
        redis_url: str = "redis://valkey:6379/0",
        stream: str = STREAM_TASKS,
        group: str = GROUP_WORKERS,
    ) -> None:
        self._redis_url = redis_url
        self._stream = stream
        self._group = group
        self._client: Any = None

    def _r(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self._redis_url, decode_responses=False)
            self._ensure_group()
        return self._client

    def _ensure_group(self) -> None:
        client = self._client
        try:
            client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def submit(self, task: Task) -> None:
        self._r().xadd(
            self._stream,
            {
                "task_id": task.task_id,
                "domain": task.domain,
                "query": task.query,
                "metadata": json.dumps(task.metadata, ensure_ascii=False),
            },
        )

    def claim(self, worker_id: str) -> Task | None:
        client = self._r()
        while True:
            result = client.xreadgroup(groupname=self._group, consumername=worker_id, count=1, block=1000, streams={self._stream: ">"})
            if not result:
                return None
            for _stream, entries in result:
                for entry_id, fields in entries:
                    task = replace(_task_from_fields(fields), entry_id=entry_id)
                    if self.is_cancelled(task.task_id):
                        client.xack(self._stream, self._group, entry_id)
                        continue
                    return task
            return None

    def ack(self, task_id: str, entry_id: str | None = None) -> None:
        if entry_id:
            self._r().xack(self._stream, self._group, entry_id)

    def fail(self, task_id: str, error: str, entry_id: str | None = None) -> None:
        self.publish(task_id, "error", {"code": "pipeline_error", "message": error})
        self.ack(task_id, entry_id)

    def reclaim(self, worker_id: str, min_idle_s: float = 60.0) -> None:
        client = self._r()
        _stream, entries, _next = client.xautoclaim(
            name=self._stream,
            groupname=self._group,
            consumername=worker_id,
            min_idle_time=int(min_idle_s * 1000),
            start_id="0-0",
            count=100,
        )
        for entry_id, fields in entries:
            client.xack(self._stream, self._group, entry_id)
            self.submit(_task_from_fields(fields))

    def publish(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        body = envelope(event_type, task_id, payload)
        self._r().xadd(
            f"{EVENT_PREFIX}{task_id}",
            {key: value if isinstance(value, (str, bytes, int, float)) else json.dumps(value, ensure_ascii=False) for key, value in body.items()},
        )

    def events(self, task_id: str, heartbeat_interval_s: float = 15.0) -> Iterator[Event]:
        client = self._r()
        key = f"{EVENT_PREFIX}{task_id}"
        last: bytes | None = None
        batch = client.xrange(key, min="-", max="+")
        for entry_id, fields in batch:
            last = entry_id
            event = _event_from_fields(fields)
            yield event
            if _is_terminal(event):
                return
        last_heartbeat = time.monotonic()
        while True:
            if last is None:
                result = client.xread(count=100, block=2000, streams={key: "$"})
            else:
                result = client.xread(count=100, block=2000, streams={key: last})
            if not result:
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval_s:
                    yield Event(
                        type="heartbeat",
                        task_id=task_id,
                        ts=datetime.now(UTC).isoformat(timespec="seconds"),
                        payload={},
                    )
                    last_heartbeat = now
                continue
            for _stream, entries in result:
                for entry_id, fields in entries:
                    event = _event_from_fields(fields)
                    last = entry_id
                    yield event
                    if _is_terminal(event):
                        return

    def is_cancelled(self, task_id: str) -> bool:
        return bool(self._r().exists(f"{CANCEL_PREFIX}{task_id}"))

    def cancel(self, task_id: str) -> None:
        self._r().set(f"{CANCEL_PREFIX}{task_id}", "1")
        self.publish(task_id, "status", {"stage": "cancelled"})


def _task_from_fields(fields: dict[bytes, bytes]) -> Task:
    text = {k.decode(): v.decode("utf-8", errors="replace") for k, v in fields.items()}
    metadata: dict[str, Any] = {}
    raw_meta = text.get("metadata")
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
        except json.JSONDecodeError:
            metadata = {}
    return Task(
        task_id=text.get("task_id", ""),
        domain=text.get("domain", "it"),
        query=text.get("query", ""),
        metadata=metadata,
    )


def _event_from_fields(fields: dict[bytes, bytes]) -> Event:
    text = {k.decode(): v.decode("utf-8", errors="replace") for k, v in fields.items()}
    payload: dict[str, Any] = {}
    raw_payload = text.get("payload")
    if raw_payload:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            payload = {}
    return Event(
        type=text.get("type", "status"),
        task_id=text.get("task_id", ""),
        ts=text.get("ts", ""),
        payload=payload,
    )