"""Перезагрузка корпуса на стенде и проверка ADR-046 пунктов 3, 6 и 9.

Порядок обязателен: сначала чистим (миграций нет по решению владельца), потом грузим,
 потом меряем. Меряем на свежих данных — иначе это будет чтение старого артефакта.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("INGEST_URL", "http://localhost:8002")
KEY = os.environ["GRAPH_AUTH_API_KEY"]
#: Файлы корпуса берём от корня репозитория, а не от текущего каталога: прибор запускали из
#: `docs/`, и он падал с FileNotFoundError, хотя все три файла лежат в репозитории.
#: Пути в `DOCS` — от корня репозитория (`docs/...`), а сам файл лежит в `prototype/infra/eval/`,
#: поэтому подниматься нужно на три уровня, а не на два.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DOCS = [
    "docs/api_reference.md",
    "docs/prototype_requirements.md",
    "docs/expert_reviews.md",
]
DOMAIN = "it"


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def submit(name: str) -> str | None:
    """Отправляет документ, повторяя при 429: `INGEST_MAX_CONCURRENT` по умолчанию 2,
    и корпус из трёх документов в него не помещается. Отказ по лимиту — не ошибка загрузки."""
    with (REPO_ROOT / name).open(encoding="utf-8") as handle:
        content = handle.read()
    payload = {
        "source_url": name,
        "domain": DOMAIN,
        "doc_type": name.rsplit(".", 1)[-1],
        "content": content,
    }
    for attempt in range(60):
        status, body = call("POST", "/api/v1/ingestion/documents", payload)
        if status == 202:
            print(f"  принято {name} -> {body['job_id']}")
            return str(body["job_id"])
        if status != 429:
            print(f"FAIL загрузка {name}: {status} {body}")
            return None
        time.sleep(10)
    print(f"FAIL {name}: лимит слотов не освободился за 10 минут")
    return None


def main() -> int:
    jobs: dict[str, str] = {}
    for name in DOCS:
        job_id = submit(name)
        if job_id is None:
            return 1
        jobs[job_id] = name

    deadline = time.monotonic() + 3600
    pending = dict(jobs)
    while pending and time.monotonic() < deadline:
        for job_id, name in list(pending.items()):
            status, body = call("GET", f"/api/v1/ingestion/jobs/{job_id}")
            state = body.get("status")
            if state in {"succeeded", "failed", "cancelled"}:
                print(f"  {name}: {state} (stage={body.get('stage')})")
                if state != "succeeded":
                    print(f"FAIL {name} не завершилась: {body}")
                    return 1
                del pending[job_id]
        if pending:
            time.sleep(5)
    if pending:
        print(f"FAIL не дождались: {sorted(pending.values())}")
        return 1

    # Ревизия читается по боевому маршруту. Прежний путь `/api/v1/revision` не существует:
    # прибор получал 404, печатал его и всё равно возвращал 0 — проверка внутри прибора была
    # мёртвой, а прогон выглядел успешным. Проверка, которая не может провалиться, хуже её
    # отсутствия: она сообщает «всё хорошо» о том, чего не спрашивала.
    status, body = call("GET", f"/api/v1/ingestion/revision?domain={DOMAIN}")
    print(f"  /revision: {status} {body}")
    if status != 200 or "revision" not in body:
        print(f"FAIL ревизия домена {DOMAIN} не прочитана: {status} {body}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())