"""Исполнитель E2E-прогона предиката уборки (B3+C, `docs/test_plan.md` §7.2).

Сценарий контролируемый и полностью описан в тест-плане; этот файл исполняет именно его,
а не что-то рядом: два посева из `.eval-corpus-old/`, снимки «до» / «между» / «после» /
«после второго прохода», уборка через маршрут `POST /api/v1/maintenance/orphan-cleanup`,
повторный проход и вердикт T1–T8.

**Что здесь принципиально.** Уборка без следа в фактах — это не результат, поэтому вызов
фиксируется целиком: метод, URL, тело, `job_id`, имя docker-контейнера и `data_revision`
домена **до** вызова, и сырой ответ. Снимки считают тем же предикатом, что и
`Neo4jGraphStore.delete_orphans`, иначе T5 сравнивал бы разные множества. Артефакт пишется
сразу после прогона, а «когда-нибудь потом» означал бы, что не напишется никогда.

Запуск — внутри сети стенда (порты наружу не публикуются), отсюда обращение к Neo4j по
HTTP-транзакционному API и только стандартная библиотека. Тома `models_data` сброс не
трогает: в нём 6.2 ГБ моделей, и `down --volumes` увел бы их вместе с данными.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHUNK_LABEL = "Chunk"
CHUNK_LABEL = "Chunk"

#: Префикс `job_id` операторской уборки. Совпадает с решением ADR-014: факт уборки должен
#: отличаться от джобы ingest, иначе по `job_id` нельзя понять, кто удалял.
MAINTENANCE_PREFIX = "maintenance:"

#: Статусы джобы ingest, на которых прогон считает состояние установившимся.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

#: Ручные сущности посева. `tag_id` задан явно, потому что `_context_node_id` строит
#: `tag:<domain>:<tag_id>` и по умолчанию нормализует `canonical_name`, а нормализация -
#: это догадка, на которой нельзя строить контролируемый сценарий: две версии посева должны
#: давать один и тот же `node_id`, иначе поддержка не совпадёт.
SEED_TAGS: list[dict[str, Any]] = [
    {"tag_id": "cleanupprobeatag", "canonical_name": "CleanupProbeA"},
    {"tag_id": "cleanupprobetagb", "canonical_name": "CleanupProbeB"},
    {"tag_id": "cleanupprobetagc", "canonical_name": "CleanupProbeC"},
]


def _edge(kind: str, left: str, right: str) -> dict[str, Any]:
    return {
        "from_id": f"tag:it:{left}",
        "to_id": f"tag:it:{right}",
        "kind": kind,
    }


#: Три ручные связи с разными носителями - именно это делает сценарий проверяемым.
#:
#: L1 объявлена в обоих документах, L2 только в перезагружаемом, L3 только в держащем.
#: После ревизии перезагружаемого документа (теперь без ручного ввода) L2 теряет всю
#: поддержку и становится кандидатом на уборку, а L1 остаётсяsupported носителем и должна
#: выжить. Одна связь, объявленная в обоих документах, дала бы только T4 и упала бы на
#: T1: кандидатов не существует, то есть удалять нечего и «уборка удалила 0» ничего не
#: доказывает.
SEED_LINKS_BY_SOURCE: dict[str, list[dict[str, Any]]] = {
    "docs/01_ontology_and_domain_profile.md": [
        _edge("SUPPORTS", "cleanupprobeatag", "cleanupprobetagb"),
        _edge("CITES", "cleanupprobetagb", "cleanupprobetagc"),
    ],
    "docs/plans/cosine-dedup.md": [
        _edge("SUPPORTS", "cleanupprobeatag", "cleanupprobetagb"),
        _edge("DERIVES_FROM", "cleanupprobeatag", "cleanupprobetagc"),
    ],
}



def _required(name: str) -> str:
    """Переменная окружения без дефолта.

    У сценария нет осмысленного значения «по умолчанию»: молчаливый адрес или молчаливый
    домен означали бы прогон по не тому стенду с правдоподобным вердиктом.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} не задан: сценарий не угадывает адреса и домен")
    return value


def _log(message: str) -> None:
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# --------------------------------------------------------------------------- HTTP


def _request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    api_key: str | None = None,
    basic: tuple[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("X-API-Key", api_key)
    if basic is not None:
        import base64

        token = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xy: тело обычно объясняет причину
        return int(exc.code), exc.read().decode("utf-8", "replace")


def cypher(statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Запрос к Neo4j по HTTP-транзакционному API.

    Отдельный драйвер не нужен: `POST /db/neo4j/tx/commit` — штатный интерфейс, и он не
    тянет за собой версию драйвера, которой в образе исполнителя нет.
    """
    url = f"{_required('NEO4J_HTTP').rstrip('/')}/db/neo4j/tx/commit"
    user, password = _required("NEO4J_USER"), _required("NEO4J_PASSWORD")
    body: dict[str, Any] = {"statements": [{"statement": statement, "parameters": parameters or {}}]}
    status, text = _request("POST", url, payload=body, basic=(user, password))
    parsed = json.loads(text or "{}")
    if status != 200:
        raise RuntimeError(f"neo4j вернул {status}: {text[:400]}")
    errors = parsed.get("errors") or []
    if errors:
        raise RuntimeError(f"neo4j: {errors}")
    results = parsed.get("results") or []
    if not results:
        return []
    # Две особенности формата HTTP-транзакции, обе стоили прогона: `columns` - список
    # строк, а не объекты с `name`, и каждая строка в `data` - объект `{"row": [...]}`,
    # а не сам массив значений. Обе формы одинаково выглядят правдоподобно, пока
    # `int()` не натыкается на ключ "row".
    columns = list(results[0].get("columns") or [])
    rows = [entry.get("row", []) if isinstance(entry, dict) else entry for entry in (results[0].get("data") or [])]
    return [dict(zip(columns, row, strict=False)) for row in rows]


# ------------------------------------------------------------------- снимки графа

#: Предикат осиротевших связей - дословно как в `Neo4jGraphStore.delete_orphans`.
ORPHAN_EDGE = "r.domain = $domain AND r.chunk_ids IS NOT NULL AND size(coalesce(r.chunk_ids, [])) = 0"
#: узлов: у узла непустой в исходном состоянии `chunk_ids`; `Chunk` исключён явно (ADR-046 п. 9),
#: `NOT (n)--()` решает, что `DETACH DELETE` не снесёт чужое.
ORPHAN_NODE = (
    "n.domain = $domain AND n.chunk_ids IS NOT NULL AND size(coalesce(n.chunk_ids, [])) = 0 "
    f"AND NOT n:{CHUNK_LABEL} AND NOT (n)--()"
)


def snapshot(domain: str) -> dict[str, Any]:
    """Полный перепись-снимок домена.

    «Полный» здесь не про размер, а про состав: T6 сравнивает графы этим снимком, и
    сравнение только числа кандидатов не заметило бы, что уборка снесла живую связь.
    """
    values = {"domain": domain}
    kind_rows = cypher(
        "MATCH ()-[r]->() WHERE r.domain = $domain "
        "RETURN coalesce(r.kind, type(r)) AS kind, count(r) AS c "
        "ORDER BY kind",
        values,
    )
    return {
        "relations": int(cypher("MATCH ()-[r]->() WHERE r.domain = $domain RETURN count(r) AS c", values)[0]["c"]),
        "nodes": int(cypher("MATCH (n) WHERE n.domain = $domain RETURN count(n) AS c", values)[0]["c"]),
        "structural_relations": int(
            cypher(
                "MATCH ()-[r]->() WHERE r.domain = $domain AND r.chunk_ids IS NULL RETURN count(r) AS c",
                values,
            )[0]["c"]
        ),
        "orphan_relations": int(
            cypher(f"MATCH ()-[r]->() WHERE {ORPHAN_EDGE} RETURN count(r) AS c", values)[0]["c"]
        ),
        "orphan_nodes": int(
            cypher(f"MATCH (n) WHERE {ORPHAN_NODE} RETURN count(n) AS c", values)[0]["c"]
        ),
        "orphan_node_ids": [
            str(row["node_id"])
            for row in cypher(
                f"MATCH (n) WHERE {ORPHAN_NODE} RETURN n.node_id AS node_id ORDER BY node_id",
                values,
            )
        ],
        "orphan_relations_by_type": {
            str(row["t"]): int(row["c"])
            for row in cypher(
                f"MATCH ()-[r]->() WHERE {ORPHAN_EDGE} "
                "RETURN type(r) AS t, count(r) AS c ORDER BY t",
                values,
            )
        },
        "contains": int(
            cypher("MATCH ()-[r:CONTAINS]->() WHERE r.domain = $domain RETURN count(r) AS c", values)[0]["c"]
        ),
        "mentions": int(
            cypher("MATCH ()-[r:MENTIONS]->() WHERE r.domain = $domain RETURN count(r) AS c", values)[0]["c"]
        ),
        "by_kind": {str(row["kind"]): int(row["c"]) for row in kind_rows},
    }


def find_cross_document_relation(domain: str) -> dict[str, Any] | None:
    """Связь, которую держат ≥2 **разных** документа (шаг 5, T4).

    Критерий `size(distinct chunk_id → source_url) >= 2`, а не «≥2 чанка»: два чанка одного
    документа проходят более слабый критерий, после чего связь переживает уборку по той
    причине, что её перезапишет прогон 2, и T4 оказывается пустым по построению.

    Идентичность связи - тройка `(from, to, type)`, а не `node_id`: у ребра в этом проекте
    свойства `node_id` нет вовсе, оно адресуется концами и типом. Поиск по `r.node_id`
    возвращал `null`, и проверка выживания спрашивала про `None` - то есть T4 падал бы
    всегда, независимо от того, удалила уборка связь или нет.
    """
    rows = cypher(
        "MATCH (a)-[r]->(b) "
        "WHERE r.domain = $domain AND r.chunk_ids IS NOT NULL AND size(coalesce(r.chunk_ids, [])) > 0 "
        "MATCH (c:Chunk) WHERE c.domain = $domain AND c.chunk_id IN r.chunk_ids "
        "WITH a, r, b, collect(DISTINCT c.source_url) AS docs, collect(DISTINCT c.chunk_id) AS chunks "
        "WHERE size(docs) >= 2 "
        "RETURN a.node_id AS from_id, b.node_id AS to_id, type(r) AS rel_type, "
        "coalesce(r.kind, type(r)) AS kind, chunks, docs "
        "ORDER BY size(docs) DESC, from_id, to_id LIMIT 1",
        {"domain": domain},
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "from_id": str(row["from_id"]),
        "to_id": str(row["to_id"]),
        "rel_type": str(row["rel_type"]),
        "kind": str(row["kind"]),
        "chunk_ids": sorted(str(c) for c in row["chunks"]),
        "source_urls": sorted(str(d) for d in row["docs"]),
    }


def relation_still_exists(domain: str, witness: dict[str, Any]) -> bool:
    rows = cypher(
        "MATCH (a)-[r]->(b) WHERE r.domain = $domain AND a.node_id = $from_id "
        "AND b.node_id = $to_id AND type(r) = $rel_type RETURN count(r) AS c",
        {
            "domain": domain,
            "from_id": witness["from_id"],
            "to_id": witness["to_id"],
            "rel_type": witness["rel_type"],
        },
    )
    return int(rows[0]["c"]) > 0



# ------------------------------------------------------------------------- джобы


def _ingestion(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    url = _required("INGESTION_URL").rstrip("/") + path
    status, text = _request(method, url, payload=payload, api_key=_required("API_KEY"))
    if status >= 400:
        raise RuntimeError(f"{method} {path} -> {status}: {text[:400]}")
    return json.loads(text or "{}")


def ingest(
    *,
    source_url: str,
    domain: str,
    doc_type: str,
    content: str,
    tags: list[str] | None = None,
    links: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Загрузка документа и ожидание терминального статуса."""
    body: dict[str, Any] = {
        "source_url": source_url,
        "domain": domain,
        "doc_type": doc_type,
        "content": content,
        "tags": tags or [],
        "links": links or [],
        "metadata": metadata or {},
    }
    _log(f"POST /documents {source_url}")
    started = _ingestion("POST", "/api/v1/ingestion/documents", body)
    job_id = str(started["job_id"])
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        job = _ingestion("GET", f"/api/v1/ingestion/jobs/{job_id}")
        if str(job.get("status")) in TERMINAL_STATUSES:
            return job
        time.sleep(3)
    raise RuntimeError(f"джоба {job_id} не завершилась за 30 минут")


def run_cleanup(domain: str) -> dict[str, Any]:
    """Уборка через маршрут. Возвращает факт вместе со следом вызова.

    `job_id` сервер ставит сам (`maintenance:<uuid4>`), и именно его возвращает ответ —
    подставлять свой нельзя, иначе запись факта и вызов разойдутся.
    """
    url = _required("INGESTION_URL").rstrip("/") + "/api/v1/maintenance/orphan-cleanup"
    body = {"domain": domain}
    _log(f"POST {url} {json.dumps(body)}")
    before = _ingestion("GET", f"/api/v1/ingestion/revision?domain={domain}")
    status, text = _request("POST", url, payload=body, api_key=_required("API_KEY"), timeout=1800)
    if status != 200:
        raise RuntimeError(f"уборка вернула {status}: {text[:400]}")
    fact = json.loads(text)
    _log(f"уборка {fact.get('job_id')}: план {fact.get('planned_relations')}, факт {fact.get('removed_relations')}")
    return {
        "request": {"method": "POST", "url": url, "body": body},
        "data_revision_before": before,
        "container": os.environ.get("INGESTION_CONTAINER", ""),
        "response": fact,
        "raw_stdout": text,
    }


def stage_durations(job_ids: list[str]) -> list[dict[str, Any]]:
    """`job_stage_durations` прямым чтением SQLite: `GET /jobs/{id}` длительности не отдаёт."""
    path = os.environ.get("SQLITE_PATH", "").strip()
    if not path or not Path(path).is_file():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT job_id, stage, duration_ms FROM job_stage_durations "
            "WHERE job_id IN ({}) ORDER BY job_id, stage".format(
                ",".join("?" * len(job_ids)) or "NULL"
            ),
            job_ids,
        ).fetchall()
    finally:
        connection.close()
    return [{"job_id": r[0], "stage": r[1], "duration_ms": r[2]} for r in rows]


# ---------------------------------------------------------------------------- main


def main() -> int:
    domain = os.environ.get("DOMAIN", "it").strip() or "it"
    reload_source = _required("RELOAD_SOURCE_URL")
    support_source = _required("SUPPORT_SOURCE_URL")
    corpus = Path(_required("CORPUS_DIR"))
    out = Path(_required("ARTIFACTS_DIR"))
    if out.exists():
        # Слияние двух прогонов в одну папку уничтожает ровно то свидетельство, ради
        # которого папка заводится, поэтому это падение, а не перезапись.
        raise RuntimeError(f"каталог артефакта уже существует: {out}")
    out.mkdir(parents=True)
    commands: list[dict[str, Any]] = []

    def write(name: str, payload: Any) -> None:
        (out / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    # --- stand.json: без него T1/T4 невыполнимы, и это неотличимо от успеха.
    stand = {
        "compose_files": os.environ.get("COMPOSE_FILES", ""),
        "project": os.environ.get("PROJECT", ""),
        "ingestion_url": _required("INGESTION_URL"),
        "domain": domain,
        "extract_llm": os.environ.get("EXTRACT_LLM", ""),
        "retention_mode": os.environ.get("DATA_RETENTION_MODE", ""),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    write("stand.json", stand)
    _log(f"стенд: project={stand['project']} EXTRACT_LLM={stand['extract_llm']!r}")

    # --- шаг 3: оба посева, версия 1. Ручной ввод входит в версию 1 и не входит в
    # перезагружаемую версию 2: именно этим и создаётся кандидат на уборку.
    jobs: list[dict[str, Any]] = []
    for source_url, corpus_rel in ((support_source, support_source), (reload_source, reload_source)):
        path = corpus / corpus_rel
        if not path.is_file():
            raise RuntimeError(f"посев не найден: {path}")
        job = ingest(
            source_url=source_url,
            domain=domain,
            doc_type="md",
            content=path.read_text(encoding="utf-8"),
            tags=SEED_TAGS,
            links=SEED_LINKS_BY_SOURCE[source_url],
        )
        commands.append({"step": "ingest-v1", "source_url": source_url, "job": job})
        _log(f"  {source_url}: {job.get('status')}, ручных связей {len(SEED_LINKS_BY_SOURCE[source_url])}")
        jobs.append(job)

    # --- шаг 4: снимок «до».
    before = snapshot(domain)
    write("snapshots.json", {"before": before})
    _log(f"снимок «до»: связей {before['relations']}, кандидатов {before['orphan_relations']}")

    # --- шаг 5: есть ли связь, которую держат два документа.
    witness = find_cross_document_relation(domain)
    write("t4_witness.json", witness or {})
    run_valid = True
    t4: dict[str, Any] = {"verdict": "inconclusive", "reason": "связь на два документа не найдена"}
    if witness is None:
        run_valid = False
        _log("T4 не собрана: многоисточниковой связи нет, прогон останавливается на шаге 5")
    else:
        t4 = {"verdict": "not_evaluated", "reason": "будет проверена после уборки"}
        _log(f"T4 собрана: {witness['rel_type']} {witness['from_id']}->{witness['to_id']} <- {witness['source_urls']}")

    verdict: dict[str, Any] = {"T1": {}, "T2": {}, "T3": {}, "T4": t4, "T5": {}, "T6": {}, "T7": {}, "T8": {}}
    cleanups: list[dict[str, Any]] = []

    if run_valid:
        # --- шаг 6: перезагрузка ТОЛЬКО основного документа новой версией.
        fresh = Path(_required("FRESH_DOC_PATH"))
        job2 = ingest(
            source_url=reload_source,
            domain=domain,
            doc_type="md",
            content=fresh.read_text(encoding="utf-8"),
            tags=[],
            links=[],
        )
        commands.append({"step": "ingest-v2", "source_url": reload_source, "job": job2})
        jobs.append(job2)
        _log(f"  {reload_source} v2: {job2.get('status')}")
        if str(job2.get("status")) != "succeeded":
            run_valid = False
            _log("прогон 2 не достиг succeeded: run_valid=false")

    snapshots: dict[str, Any] = {"before": before}
    if run_valid:
        # --- шаг 7: снимок «между». Пустой он или нет — это T1.
        between = snapshot(domain)
        snapshots["between"] = between
        write("snapshots.json", snapshots)
        verdict["T1"] = (
            {"verdict": "pass", "reason": "есть связи с пустым chunk_ids"}
            if between["orphan_relations"] + between["orphan_nodes"] > 0
            else {"verdict": "fail", "reason": "кандидатов нет: уборке нечего удалять, тест неинформативен"}
        )
        if verdict["T1"]["verdict"] != "pass":
            run_valid = False

    if run_valid:
        # --- шаг 8: уборка. След вызова пишется вместе с ним, а не «потом вспомнить».
        cleanups.append(run_cleanup(domain))
        commands.append({"step": "cleanup", **cleanups[-1]})
        after = snapshot(domain)
        snapshots["after"] = after
        write("snapshots.json", snapshots)

        verdict["T3"] = (
            {"verdict": "pass", "reason": "связей с пустым chunk_ids не осталось"}
            if after["orphan_relations"] == 0
            else {"verdict": "fail", "reason": f"осталось {after['orphan_relations']} связей-кандидатов"}
        )
        witness_alive = relation_still_exists(domain, witness)  # type: ignore[arg-type]
        verdict["T4"] = {
            "verdict": "pass" if witness_alive else "fail",
            "reason": "связь жива после ревизии носителя"
            if witness_alive
            else "КРИТИЧНО: предикат удалил живую связь",
        }

        # T2 сравнивает «между» и «после», а не «до» и «после». Между этими снимками
        # находится ревизия документа, которая законно меняет число чанков, а с ним и
        # число `CONTAINS`; сравнение через ревизию измеряло бы не уборку, а разницу длин
        # документов. Что уборка не трогает структурные - видно по соседним снимкам.
        #
        # Вторая половина проверяет сам дискриминатор: ни одна структурная связь не должна
        # попасть в кандидаты. Раньше здесь стояла проверка «перезагруженный документ не
        # упоминается в `source_ids` структурных», и она неразрешима: после ревизии
        # документ **законно** остаётся в `source_ids` своих связей, потому что это уже его
        # новая, действующая версия. Проверка искала остаток старой версии, а находила
        # новую, то есть всегда давала `fail`.
        structural_ok = (
            after["contains"] == between["contains"] and after["mentions"] == between["mentions"]
        )
        candidates_by_type = between["orphan_relations_by_type"]
        structural_leaked = {t: c for t, c in candidates_by_type.items() if t in ("CONTAINS", "MENTIONS")}
        verdict["T2"] = {
            "verdict": "pass" if structural_ok and not structural_leaked else "fail",
            "reason": (
                f"CONTAINS {between['contains']}->{after['contains']}, "
                f"MENTIONS {between['mentions']}->{after['mentions']}; "
                f"структурные среди кандидатов: {structural_leaked or 'нет'}; "
                f"кандидаты по типам: {candidates_by_type or 'нет'}"
            ),
        }

        first = cleanups[0]["response"]
        expected_delta = between["orphan_relations"] + between["orphan_nodes"]
        # T5: planned == removed И равно наблюдаемой дельте. Второе равенство обязательно:
        # `delete_orphans` возвращает edge_count + node_count под именем `*_relations`, а
        # `planned_nodes`/`removed_nodes` - литералы `0`, поэтому одно имя считает и связи,
        # и узлы, и подсчёт «по имени» ничего не проверяет.
        ok = (
            first["planned_relations"] == first["removed_relations"] == expected_delta
        )
        verdict["T5"] = {
            "verdict": "pass" if ok else "fail",
            "reason": f"plan={first['planned_relations']} fact={first['removed_relations']} "
            f"дельта={expected_delta} (связи {between['orphan_relations']} + узлы {between['orphan_nodes']})",
        }

        # --- шаг 10: повторный проход (идемпотентность, T6).
        cleanups.append(run_cleanup(domain))
        commands.append({"step": "cleanup-repeat", **cleanups[-1]})
        final = snapshot(domain)
        snapshots["after_second"] = final
        write("snapshots.json", snapshots)
        second = cleanups[1]["response"]
        verdict["T6"] = {
            "verdict": "pass"
            if second["planned_relations"] == 0 and second["removed_relations"] == 0 and final == after
            else {"verdict": "fail", "reason": f"повторный проход: план {second['planned_relations']}, "
            f"факт {second['removed_relations']}, граф изменился: {final != after}"},
            "reason": "planned=removed=0 и снимок графа не изменился"
            if second["planned_relations"] == 0
            else "повторный проход что-то удалил",
        }
        verdict["T7"] = {
            "verdict": "not_evaluated",
            "reason": "в этом сценарии недоказуемо: без конкурентного ingest planned и removed "
            "обязаны совпасть, схлопывание неотличимо от корректности. Покрыто юнит-тестом.",
        }

    # --- T8: длительности стадий обеих ingest-джоб. Для джобы уборки неприменимо: строки
    # в `jobs` нет, поэтому `stages()` и `stage_durations()` пусты - и это ожидаемо.
    durations = stage_durations([str(j.get("job_id")) for j in jobs if j.get("job_id")])
    write("stage_durations.json", durations)
    ingest_ok = all(str(j.get("status")) == "succeeded" for j in jobs) and bool(durations)
    verdict["T8"] = {
        "verdict": "pass" if ingest_ok else "fail",
        "reason": f"{len(durations)} записей длительностей у {len(jobs)} ingest-джоб; "
        f"для джобы уборки неприменимо",
    }
    if not ingest_ok:
        run_valid = False

    if not run_valid:
        for name, item in verdict.items():
            if item.get("verdict") == "not_evaluated" and name != "T4":
                item["reason"] = f"{item['reason']} (прогон признан несостоявшимся до конца)"

    write("cleanup_fact.json", {"cleanups": cleanups})
    write("commands.log", "\n".join(json.dumps(c, ensure_ascii=False) for c in commands) + "\n")
    write("verdict.json", {"run_valid": run_valid, "verdicts": verdict, "finished_at": datetime.now(UTC).isoformat(timespec="seconds")})
    _log(f"вердикт: run_valid={run_valid} " + ", ".join(f"{k}={v.get('verdict')}" for k, v in sorted(verdict.items())))
    _log(f"артефакт: {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _log(f"ПРОГОН УПАЛ: {type(exc).__name__}: {exc}")
        raise
