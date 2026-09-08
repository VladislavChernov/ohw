"""Query Service: Task Store (SQLite `query_tasks`) + Task Queue (Valkey/Redis Streams).

Canonical статус задач живёт в SQLite (ADR-023): Valkey — транспорт, не БД.
lifecycle: queued -> running -> succeeded | failed | cancelled.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

TASK_ID_PREFIX = "q_"


def new_task_id() -> str:
    return f"{TASK_ID_PREFIX}{secrets.token_hex(4)}"


@dataclass(frozen=True)
class Task:
    task_id: str
    domain: str
    query: str
    metadata: dict[str, Any] = field(default_factory=dict)
    entry_id: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Event:
    type: str
    task_id: str
    ts: str
    payload: dict[str, Any]