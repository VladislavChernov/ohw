"""SQLite-практики: WAL + busy_timeout (по ревью review_2 B-2 / review_team O-2)."""

from __future__ import annotations

from pathlib import Path

from graphrag_proto.sqlite_utils import connect_sqlite


def test_wal_mode_and_busy_timeout(tmp_path: Path) -> None:
    conn = connect_sqlite(tmp_path / "t.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_sqlite_survives_two_connections(tmp_path: Path) -> None:
    path = tmp_path / "share.db"
    first = connect_sqlite(path)
    second = connect_sqlite(path)
    try:
        first.execute("CREATE TABLE IF NOT EXISTS t (x TEXT)")
        first.commit()
        second.execute("INSERT INTO t VALUES ('a')")
        second.commit()
        assert first.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    finally:
        first.close()
        second.close()