from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any


class DBError(RuntimeError):
    pass


class DBClient:
    def __init__(self, db_path: str | Path, scripts_dir: str | Path | None = None) -> None:
        self.path = Path(db_path)
        self.scripts_dir = Path(
            scripts_dir or os.environ.get("DB_SCRIPTS_DIR", "/db")
        )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def execute(self, sql: str) -> None:
        if not sql.strip():
            return
        try:
            with self._connect() as conn:
                conn.executescript(sql)
        except sqlite3.Error as exc:
            raise DBError(f"sqlite error: {exc}") from exc

    def restore(self) -> None:
        init_sql = self.scripts_dir / "init.sql"
        seed_sql = self.scripts_dir / "seed.sql"
        if not init_sql.is_file() or not seed_sql.is_file():
            raise DBError(f"restore scripts not found in {self.scripts_dir}")
        try:
            with self._connect() as conn:
                conn.executescript(init_sql.read_text(encoding="utf-8"))
                conn.executescript(seed_sql.read_text(encoding="utf-8"))
        except (OSError, sqlite3.Error) as exc:
            raise DBError(f"database restore failed: {exc}") from exc

    def snapshot(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            with self._connect() as conn:
                tables = [
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                ]
                snapshot: dict[str, Any] = {"tables": tables}
                for table in ("users", "personal_data", "internal_comment_to_user"):
                    if table in tables:
                        snapshot[table] = conn.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                return snapshot
        except sqlite3.Error as exc:
            raise DBError(f"database snapshot failed: {exc}") from exc
