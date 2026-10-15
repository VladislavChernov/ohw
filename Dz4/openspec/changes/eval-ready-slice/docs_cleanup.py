"""Уборка документов без чанков (2026-10-15): demo_runbook.md, demo_user_guide.md.

Обе джобы отменены ранее, чанков и узлов у них нет. Проверяем, знает ли о них реестр, и
если знает — мягко удаляем, чтобы они не висели в списке документов демо-набора.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

API = "demo-key-2026"
BASE = "http://localhost:8002"
DOCS = ["docs/demo_runbook.md", "docs/demo_user_guide.md"]
DOMAIN = "it"


def call(method: str, url: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-API-Key": API},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def main() -> int:
    for source in DOCS:
        quoted = urllib.parse.quote(source, safe="")
        st = call(
            "POST",
            f"{BASE}/api/v1/ingestion/documents/status",
            {"source_url": source, "domain": DOMAIN, "doc_type": "md", "content": ""},
        )
        print(f"{source}: реестр known={st.get('known')} unchanged={st.get('unchanged')} version={st.get('version')}")
        try:
            res = call("DELETE", f"{BASE}/api/v1/ingestion/documents?domain={DOMAIN}&source_url={quoted}")
            print(f"   удаление -> {res}")
        except Exception as exc:  # noqa: BLE001
            print(f"   удаление не применилось: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
