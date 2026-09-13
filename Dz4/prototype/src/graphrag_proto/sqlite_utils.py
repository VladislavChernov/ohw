"""Общие SQLite-практики для хранилищ прототипа (по ревью review_2 B-2 / review_team O-2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_sqlite(db_path: str | Path) -> sqlite3.Connection:
    """Открывает SQLite-соединение с WAL, busy_timeout и foreign_keys.

    WAL допускает читателей во время записи; `busy_timeout` ждёт до 5 c вместо
    мгновенного `database is locked` — критично при фоновых потоках in-process
    execution и параллельных healthcheck/запросах к тому же файлу.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn