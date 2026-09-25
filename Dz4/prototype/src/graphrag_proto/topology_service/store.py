"""SQLite-хранилище override'ов адаптеров, Redis и projection + монотонный revision
(ADR-019, add-topology-adapters).

PUT /api/v1/config/adapters пишет переопределения слотов; каждый фактический
переключающий PUT увеличивает `revision` — сигнал потребителям (Query Worker)
пересобрать провайдеров «на лету».
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from graphrag_proto.sqlite_utils import connect_sqlite

_META_REVISION = "revision"


class TopologyStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = connect_sqlite(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS adapter_overrides ("
            "slot TEXT PRIMARY KEY, provider TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS redis_overrides ("
            "key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS projection_overrides ("
            "key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            (_META_REVISION, "0"),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def overrides(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT slot, provider FROM adapter_overrides"
            ).fetchall()
        return {str(slot): str(provider) for slot, provider in rows}

    def redis_overrides(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json FROM redis_overrides"
            ).fetchall()
        result: dict[str, Any] = {}
        for key, value_json in rows:
            try:
                value = json.loads(value_json)
            except json.JSONDecodeError:
                continue
            if isinstance(value, (str, int, float, bool)):
                result[str(key)] = value
        return result

    def apply_redis_overrides(self, updates: Mapping[str, Any]) -> int:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            changed = False
            for key, value in updates.items():
                encoded = json.dumps(value, ensure_ascii=False)
                row = self._conn.execute(
                    "SELECT value_json FROM redis_overrides WHERE key = ?", (key,)
                ).fetchone()
                if row is not None and row[0] == encoded:
                    continue
                self._conn.execute(
                    "INSERT OR REPLACE INTO redis_overrides (key, value_json, updated_at) "
                    "VALUES (?, ?, ?)",
                    (key, encoded, now),
                )
                changed = True
            if changed:
                revision = self.revision() + 1
                self._conn.execute(
                    "UPDATE meta SET value = ? WHERE key = ?",
                    (str(revision), _META_REVISION),
                )
            else:
                revision = self.revision()
            self._conn.commit()
        return revision

    def projection_overrides(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json FROM projection_overrides"
            ).fetchall()
        result: dict[str, Any] = {}
        for key, value_json in rows:
            try:
                value = json.loads(value_json)
            except json.JSONDecodeError:
                continue
            if isinstance(value, (str, int, float, bool)):
                result[str(key)] = value
        return result

    def apply_projection_overrides(self, updates: Mapping[str, Any]) -> int:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            changed = False
            for key, value in updates.items():
                encoded = json.dumps(value, ensure_ascii=False)
                row = self._conn.execute(
                    "SELECT value_json FROM projection_overrides WHERE key = ?", (key,)
                ).fetchone()
                if row is not None and row[0] == encoded:
                    continue
                self._conn.execute(
                    "INSERT OR REPLACE INTO projection_overrides (key, value_json, updated_at) "
                    "VALUES (?, ?, ?)",
                    (key, encoded, now),
                )
                changed = True
            if changed:
                revision = self.revision() + 1
                self._conn.execute(
                    "UPDATE meta SET value = ? WHERE key = ?",
                    (str(revision), _META_REVISION),
                )
            else:
                revision = self.revision()
            self._conn.commit()
        return revision

    def revision(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (_META_REVISION,)
            ).fetchone()
        return int(row[0]) if row else 0

    def apply_overrides(self, updates: dict[str, str]) -> int:
        """Применяет ограниченный набор слотов; возвращает новую revision.

        revision увеличивается только если хотя бы один слот реально изменился.
        `updates` уже валидированы каталогом (валидацию делает сервис).
        """
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            changed = False
            for slot, provider in updates.items():
                rows = self._conn.execute(
                    "SELECT provider FROM adapter_overrides WHERE slot = ?", (slot,)
                ).fetchall()
                current = rows[0][0] if rows else None
                if current == provider:
                    continue
                self._conn.execute(
                    "INSERT OR REPLACE INTO adapter_overrides (slot, provider, updated_at) "
                    "VALUES (?, ?, ?)",
                    (slot, provider, now),
                )
                changed = True
            if changed:
                revision = self.revision() + 1
                self._conn.execute(
                    "UPDATE meta SET value = ? WHERE key = ?",
                    (str(revision), _META_REVISION),
                )
            else:
                revision = self.revision()
            self._conn.commit()
        return revision