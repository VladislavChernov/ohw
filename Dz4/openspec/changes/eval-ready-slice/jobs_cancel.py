"""Осмотрado джоб ингеста и отмена зависших (2026-10-15, пагинация учтена)."""

from __future__ import annotations

import json
import urllib.request

API = "demo-key-2026"
BASE = "http://localhost:8002"
TERMINAL = {"succeeded", "failed", "cancelled"}


def call(method: str, url: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-API-Key": API},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def all_jobs() -> list[dict]:
    page = 1
    items: list[dict] = []
    while True:
        resp = call("GET", f"{BASE}/api/v1/ingestion/jobs?page={page}&page_size=200")
        batch = resp.get("items", [])
        items.extend(batch)
        if len(items) >= resp.get("total", 0) or not batch:
            return items
        page += 1


def main() -> int:
    items = all_jobs()
    statuses: dict[str, int] = {}
    for job in items:
        statuses[job.get("status", "?")] = statuses.get(job.get("status", "?"), 0) + 1
    print(f"всего джоб: {len(items)}; по статусам: {statuses}")

    stuck = [j for j in items if j.get("status") not in TERMINAL]
    print(f"нетерминальных: {len(stuck)}")
    for job in stuck:
        print("   ", job.get("job_id"), job.get("source_url"), job.get("status"), job.get("stage"))
        try:
            print("   отмена ->", call("DELETE", f"{BASE}/api/v1/ingestion/jobs/{job['job_id']}"))
        except Exception as exc:  # noqa: BLE001
            print(f"   отмена не удалась: {exc}")

    after = all_jobs()
    statuses2: dict[str, int] = {}
    for job in after:
        statuses2[job.get("status", "?")] = statuses2.get(job.get("status", "?"), 0) + 1
    print("после отмены:", statuses2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
