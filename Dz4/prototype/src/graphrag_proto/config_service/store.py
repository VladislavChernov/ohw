from __future__ import annotations

import sqlite3
from pathlib import Path


class ConfigStore:
    """SQLite-хранилище runtime config (namespace key/value).

    Прототипная схема M0: одна таблица `runtime_config`; миграции появятся
    вместе с полным набором namespace (docs/04 §5) в следующих вехах.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS runtime_config ("
            "namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
            "PRIMARY KEY (namespace, key))"
        )
        self._conn.commit()

    def get(self, namespace: str, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM runtime_config WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
        return row[0] if row else None

    def set(self, namespace: str, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO runtime_config (namespace, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT (namespace, key) DO UPDATE SET value = excluded.value",
            (namespace, key, value),
        )
        self._conn.commit()

    def get_active_profile(self, default: str) -> str:
        return self.get("domain", "active_profile") or default

    def set_active_profile(self, domain: str) -> None:
        self.set("domain", "active_profile", domain)

    def close(self) -> None:
        self._conn.close()