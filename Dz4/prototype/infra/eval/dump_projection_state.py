"""Читает состояние проекции из боевой SQLite и печатает его как есть."""

from __future__ import annotations

import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/graphrag/projection/projection.db"
con = sqlite3.connect(path)
tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)
for table in tables:
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
    columns = [d[0] for d in con.execute(f"SELECT * FROM {table} LIMIT 0").description]
    print(f"\n-- {table} ({len(rows)} строк)")
    for row in rows:
        record = {k: (str(v)[:20] if v is not None else None) for k, v in zip(columns, row)}
        print("  ", record)
con.close()