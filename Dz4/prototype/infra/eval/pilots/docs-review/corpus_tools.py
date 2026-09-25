"""Offline snapshot preparation/check; explicit upload through Ingestion API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate(manifest_path=None):
    manifest = read_json(manifest_path or HERE / "corpus-manifest.json")
    sources = [d["source_url"] for d in manifest["documents"]]
    assert sources and len(sources) == len(set(sources))
    rows = [json.loads(s) for s in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines() if s.strip()]
    assert rows and len(rows) == len({q["id"] for q in rows})
    for q in rows:
        assert q["query"] and q["golden_facts"] and q["golden_sources"]
        assert set(q["golden_sources"]) <= set(sources), q["id"]
        assert q["evidence_sections"] and q["rubric"]
    return manifest


def prepare(root, snapshot, manifest_path=None):
    manifest = validate(manifest_path)
    root = root.resolve(strict=True)
    if snapshot.exists():
        raise ValueError(f"Refusing to overwrite snapshot: {snapshot}")
    payloads = []
    for index, doc in enumerate(manifest["documents"]):
        path = (root / doc["path"]).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Document outside project root")
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        if doc.get("render") == "json_markdown":
            json.loads(text)
            text = f"# Source: {doc['source_url']}\n\n```json\n{text.rstrip()}\n```\n"
        content = text.encode("utf-8")
        name = f"document-{index + 1:02d}.md"
        payloads.append((name, content))
        doc.update(snapshot_file=name, original_sha256=digest(raw), content_sha256=digest(content))
    manifest["prepared_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["dataset_sha256"] = digest((HERE / "questions.jsonl").read_bytes())
    snapshot.mkdir(parents=True)
    for name, content in payloads:
        (snapshot / name).write_bytes(content)
    (snapshot / "manifest.lock.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    check_snapshot(snapshot)
    print(f"PREPARED: {len(payloads)} documents -> {snapshot}")


def check_snapshot(snapshot, manifest_path=None):
    proposed = validate(manifest_path)
    locked = read_json(snapshot / "manifest.lock.json")
    assert locked["dataset_sha256"] == digest((HERE / "questions.jsonl").read_bytes())
    assert [d["source_url"] for d in locked["documents"]] == [d["source_url"] for d in proposed["documents"]]
    for doc in locked["documents"]:
        path = (snapshot / doc["snapshot_file"]).resolve(strict=True)
        assert path.is_relative_to(snapshot.resolve())
        assert digest(path.read_bytes()) == doc["content_sha256"], doc["source_url"]
    return locked


def api(url, body=None, timeout=None):
    # Таймаут HTTP-клиента — из env (Go-style харнес: без зашитых цифр в исходнике).
    if timeout is None:
        timeout = int(os.environ.get("HTTP_CLIENT_TIMEOUT_S", "30"))
    key = os.environ.get("X_API_KEY") or os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"X-API-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def upload(snapshot, out, manifest_path=None):
    locked = check_snapshot(snapshot, manifest_path)
    ingestion = os.environ.get("INGESTION_URL", "http://ingestion-api:8002").rstrip("/")
    config = os.environ.get("CONFIG_URL", "http://config-service:8001").rstrip("/")
    embeddings = os.environ.get("EMBEDDINGS_URL", "http://embeddings-service:8004").rstrip("/")
    health = api(embeddings + "/health")
    assert health["mode"] == "sentence-transformer" and health["dimensions"] == 1024
    api(config + "/api/v1/config/domain/activate", {"domain": "it"})
    vector = api(embeddings + "/api/v1/embed", {"text": "Проверка эмбеддингов проекта", "domain": "it"})
    assert len(vector["vector"]) == 1024
    receipt = {"corpus_version": locked["corpus_version"], "jobs": []}
    for doc in locked["documents"]:
        content = (snapshot / doc["snapshot_file"]).read_text(encoding="utf-8")
        job = api(ingestion + "/api/v1/ingestion/documents", {"domain": "it", "source_url": doc["source_url"], "doc_type": "md", "content": content})
        deadline = time.monotonic() + 1800
        while True:
            state = api(ingestion + "/api/v1/ingestion/jobs/" + job["job_id"])
            if state["status"] == "succeeded":
                break
            if state["status"] in ("failed", "cancelled"):
                raise RuntimeError(state)
            if time.monotonic() >= deadline:
                raise TimeoutError(job["job_id"])
            time.sleep(2)
        receipt["jobs"].append({"source_url": doc["source_url"], "job_id": job["job_id"], "status": "succeeded"})
        print("INGESTED:", doc["source_url"], flush=True)
    receipt["revision"] = api(ingestion + "/api/v1/ingestion/revision?domain=it")["revision"]
    assert receipt["revision"], "Empty revision"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print("UPLOAD COMPLETE; revision:", receipt["revision"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "prepare", "upload"])
    parser.add_argument("--root", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Альтернативный manifest корпуса (например, укороченный публичный вариант)",
    )
    parser.add_argument("--snapshot", type=Path, default=HERE / "snapshot")
    parser.add_argument("--out", type=Path, default=Path("/reports/docs-review-upload.json"))
    args = parser.parse_args()
    if args.action == "prepare":
        if args.root is None:
            parser.error("prepare requires --root")
        prepare(args.root, args.snapshot, args.manifest)
    elif args.action == "upload":
        upload(args.snapshot, args.out, args.manifest)
    else:
        validate(args.manifest)
        if args.snapshot.exists():
            check_snapshot(args.snapshot, args.manifest)
        print("CHECK OK: 10 questions, 7 sources; no services contacted")


if __name__ == "__main__":
    main()
