"""SQLite-хранилище override'ов адаптеров + монотонный revision
(add-topology-adapters, ADR-019).

PUT /api/v1/config/adapters пишет переопределения слотов; каждый фактический
переключающий PUT увеличивает `revision` — сигнал потребителям (Query Worker)
пересобрать провайдеров «на лету».
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_META_REVISION = "revision"


class TopologyStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS adapter_overrides ("
            "slot TEXT PRIMARY KEY, provider TEXT NOT NULL, updated_at TEXT NOT NULL)"
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
        from datetime import UTC, datetime

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