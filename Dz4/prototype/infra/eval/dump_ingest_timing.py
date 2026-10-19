"""Длительности джоб и паспорт экстракции: откуда берётся и время прогона, и воспроизводимость."""

from __future__ import annotations

import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/data/ingestion.sqlite"
con = sqlite3.connect(path)


def show(title: str, sql: str) -> None:
    print(f"\n-- {title}")
    cur = con.execute(sql)
    columns = [d[0] for d in cur.description]
    print("   ", columns)
    for row in cur.fetchall():
        print("   ", dict(zip(columns, row)))


show(
    "длительности по стадиям",
    "SELECT job_id, stage, duration_ms FROM job_stage_durations ORDER BY job_id, stage",
)
show(
    "паспорт экстракции",
    "SELECT source_url, model, temperature, max_tokens, seed, llm_enabled "
    "FROM job_extraction_passport",
)
con.close()