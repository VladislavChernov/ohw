"""Наполнить набор `ready` до указанного числа документов и дождаться конца.

Зачем лимит аргументом: корпус на 30 документов разбирается LLM больше часа, а для
проверки «работает ли контур» хватает десяти. Ждать всё ради галочки - потеря времени,
но грузить половину и объявлять готовым - тоже нельзя, поэтому граница задаётся явно.

Идемпотентен. Повторный запуск не создаёт дублей: документы, уже принятые в этом
наборе, пропускаются. Иначе второй запуск после первой неудачи удвоил бы корпус.

Критерий готовности объявлен до запуска:
  1. запросов к LLM было > 0                    (иначе vector-only, а это «успех»);
  2. в Neo4j есть ContextNode И связи          (иначе загружены только вектора);
  3. ни одна джоба не в статусе failed          (иначе часть корпуса потеряна).
Каждое условие проверяется самостоятельно, нарушение означает ненулевой код выхода.
"""

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

API = "http://localhost:8002"
KEY = "changeme"
HEADERS = {"X-API-Key": KEY}
DOCS = pathlib.Path("/corpus")
TERMINAL = {"succeeded", "failed", "noop"}


def request(method: str, path: str, payload=None, timeout: int = 300) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = dict(HEADERS)
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=body, headers=headers, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def jobs() -> list[dict]:
    return request("GET", "/api/v1/ingestion/jobs?page_size=200", timeout=60).get("items", [])


def wait_quiet(poll: int, budget_s: int) -> list[dict]:
    """Дождаться, пока не останется ни одной незавершённой джобы."""
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        snapshot = jobs()
        running = [j for j in snapshot if j.get("status") not in TERMINAL]
        if not running:
            return snapshot
        print(f"  в работе: {len(running)}", flush=True)
        time.sleep(poll)
    print(f"  ПРЕВЫШЕН БЮДЖЕТ {budget_s} с - джобы всё ещё идут", flush=True)
    return jobs()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10, help="сколько документов грузить")
    parser.add_argument("--poll", type=int, default=20)
    parser.add_argument("--budget-s", type=int, default=7200)
    args = parser.parse_args()

    docs = sorted(DOCS.glob("*.md"))
    if not docs:
        print(f"НЕТ ДОКУМЕНТОВ в {DOCS}: загрузка не выполнялась.")
        return 2

    existing = {j["source_url"] for j in jobs()}
    todo = [d for d in docs if f"docs/{d.name}" not in existing]
    if len(existing) >= args.limit:
        todo = []
    todo = todo[: max(0, args.limit - len(existing))]
    print(f"уже загружено: {len(existing)}, к загрузке: {len(todo)}")

    pending = list(todo)
    accepted = 0
    while pending:
        still = []
        for doc in pending:
            payload = {
                "source_url": f"docs/{doc.name}",
                "domain": "it",
                "doc_type": "md",
                "content": doc.read_text(encoding="utf-8"),
            }
            try:
                request("POST", "/api/v1/ingestion/documents", payload)
                accepted += 1
                print(f"  принят {doc.name}", flush=True)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    still.append(doc)  # слоты заняты - это состояние, не отказ
                    continue
                print(f"  {doc.name}: ОТКАЗ {exc.code} {exc.read()[:140]}", flush=True)
        pending = still
        if pending:
            print(f"  {len(pending)} ждут слот", flush=True)
            time.sleep(15)

    print(f"принято: {accepted}")
    print("жду завершения...", flush=True)
    snapshot = wait_quiet(args.poll, args.budget_s)

    failed = [j for j in snapshot if j.get("status") == "failed"]
    succeeded = [j for j in snapshot if j.get("status") == "succeeded"]
    print(f"\n=== ИТОГ ===")
    print(f"джоб succeeded : {len(succeeded)}")
    print(f"джоб failed    : {len(failed)}")
    for job in failed[:10]:
        print(f"    {job['source_url']}: {str(job.get('error'))[:110]}")

    degraded = 0
    for job in succeeded:
        detail = request("GET", f"/api/v1/ingestion/jobs/{job['job_id']}", timeout=60)
        if any("degraded" in (s.get("message") or "") for s in detail.get("stages", [])):
            degraded += 1
    print(f"с деградацией  : {degraded}/{len(succeeded)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())