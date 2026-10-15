"""Пилот перезагрузки одного документа (2026-10-15).

Документ: docs/01_ontology_and_domain_profile.md — 10 чанков, в наборе `ready` vector-only
(ноль ContextNode). Цель проверить механику: soft-delete -> повторная загрузка -> узлы.

Порядок: docker cp <док> ohw-runtime-ingestion:/tmp/demo_doc.md
         docker cp reload_probe.py ohw-runtime-ingestion:/tmp/
         docker exec ohw-runtime-ingestion python /tmp/reload_probe.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

API = "demo-key-2026"
BASE = "http://localhost:8002"
SOURCE = "docs/01_ontology_and_domain_profile.md"
DOMAIN = "it"


def call(method: str, url: str, body: dict | None = None, timeout: int = 180):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-API-Key": API},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def main() -> int:
    text = open("/tmp/demo_doc.md", encoding="utf-8").read()
    payload = {"source_url": SOURCE, "domain": DOMAIN, "doc_type": "md", "content": text}
    quoted = urllib.parse.quote(SOURCE, safe="")

    try:
        print("soft-delete:", call("DELETE", f"{BASE}/api/v1/ingestion/documents?domain={DOMAIN}&source_url={quoted}"))
    except Exception as exc:  # noqa: BLE001
        print(f"soft-delete не удался: {exc}")

    st = call("POST", f"{BASE}/api/v1/ingestion/documents/status", payload)
    print("status после удаления:", {k: st.get(k) for k in ("known", "unchanged", "version", "content_hash")})

    started = time.monotonic()
    job = call("POST", f"{BASE}/api/v1/ingestion/documents", payload)
    job_id = job.get("job_id")
    print(f"джоба {job_id} принята, жду...")

    current: dict = {}
    while True:
        time.sleep(5)
        current = call("GET", f"{BASE}/api/v1/ingestion/jobs/{job_id}")
        if current.get("status") in ("succeeded", "failed", "cancelled"):
            break
    wall = time.monotonic() - started

    print(f"ИТОГ: status={current.get('status')} stage={current.get('stage')} wall={wall:.1f}s")
    print("stage_durations_ms:", current.get("stage_durations_ms"))
    print("total_duration_ms:", current.get("total_duration_ms"))
    print("signals:", current.get("signals"))
    print("enrichment:", current.get("enrichment"))
    print("nodes_by_extractor:", current.get("nodes_by_extractor"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
