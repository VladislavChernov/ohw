from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TaskStore:
    """SQLite-хранилище задач Query API (docs/api_reference.md §3, ADR-023)."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS query_tasks ("
            "task_id TEXT PRIMARY KEY,"
            "domain TEXT NOT NULL,"
            "query TEXT NOT NULL,"
            "status TEXT NOT NULL,"
            "stage TEXT,"
            "created_at TEXT NOT NULL,"
            "error TEXT)"
        )
        self._conn.commit()

    def create(self, task_id: str, domain: str, query: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO query_tasks (task_id, domain, query, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, domain, query, STATUS_QUEUED, _now()),
            )
            self._conn.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT task_id, domain, query, status, stage, created_at, error "
                "FROM query_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row[0],
            "domain": row[1],
            "query": row[2],
            "status": row[3],
            "stage": row[4],
            "created_at": row[5],
            "error": row[6],
        }

    def set_status(self, task_id: str, status: str, stage: str | None = None, error: str | None = None) -> bool:
        """Переход статуса; терминальные статусы не перезаписываются (0 влияет строк)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE query_tasks SET status = ?, stage = COALESCE(?, stage), error = ? "
                "WHERE task_id = ? AND status NOT IN (?, ?, ?)",
                (status, stage, error, task_id, STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def mark_running(self, task_id: str, stage: str | None = None) -> bool:
        return self.set_status(task_id, STATUS_RUNNING, stage=stage)

    def mark_succeeded(self, task_id: str) -> bool:
        return self.set_status(task_id, STATUS_SUCCEEDED)

    def mark_failed(self, task_id: str, error: str) -> bool:
        return self.set_status(task_id, STATUS_FAILED, error=error)

    def mark_cancelled(self, task_id: str) -> bool:
        return self.set_status(task_id, STATUS_CANCELLED)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps_event(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))