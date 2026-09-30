"""E2E-сценарий глубины обхода (docs/test_plan.md 7.3, ADR-036).

Сценарий отвечает на вопрос, на который юнит-тесты ответить не могут: доезжает ли обход до
нужных узлов на живом графе. Потолок поднят с 3 до 6, счётчик ветвления переставлен на
подсчёт по узлу, и ни разу после этого не проверено, что цепочка действительно проходится.

Почему конвейер собирается здесь, а не через POST /query: на eval-стенде сервиса
`query-api` нет (compose.eval-minimal.yaml: config, glossary, neo4j, embeddings, ingestion,
llm, eval-runner), а HTTP-приём max_depth покрыт четырьмя юнит-тестами
tests/test_query_api.py. Конвейер собирается через query_service.runtime.build_pipeline -
тот же factory, что у настоящего query-service, - и отличается только отсутствием
HTTP-обвязки. `generate=False`: LLM здесь не участвует, потому что проверяется арифметика
обхода, а не генерация.

Фикстура - цепочка, а не звезда. На звезде все узлы в одном хопе от центра, и max_depth не
меняет ничего при любом значении, то есть сценарий был бы фиктивным.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Префикс сценария в именах артефактов и seed-документов.
SCENARIO = "depth"

#: Статусы джобы ingest, на которых состояние считается установившимся.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

#: Узлы цепочки по порядку. tag_id задан явно, потому что `_context_node_id` строит
#: `tag:<domain>:<tag_id>` и по умолчанию нормализует canonical_name, а нормализация - это
#: догадка, на которой нельзя строить контролируемый сценарий.
CHAIN = ("chain_a", "chain_b", "chain_c", "chain_d")

#: Документ на каждый узел, один узел - один документ. Иначе «вернулось 2 документа» и
#: «вернулось 3 документа» описывали бы одну и ту же цепочку.
CHAIN_DOCS: dict[str, str] = {
    "chain_a": "depth-probe/anchor.md",
    "chain_b": "depth-probe/second.md",
    "chain_c": "depth-probe/third.md",
    "chain_d": "depth-probe/tail.md",
}

#: Текст документа. Лексика каждого документа намеренно своя, чтобы векторный поиск по
#: вопросу не подмешивал соседние документы: иначе при глубине 1 в ответе окажутся
#: документы, до которых обход не дошёл, и число перестанет что-либо измерять.
CHAIN_BODIES: dict[str, str] = {
    "chain_a": (
        "Zvonkyi Hvorist Ukladu. Этот документ задаёт начало контролируемой цепочки обхода "
        "и объявляет ручной переход к следующему звену. Больше здесь ничего нет."
    ),
    "chain_b": (
        "Kley Hroniki Stseny. Документ объявляет ручной переход к третьему звену и больше "
        "ничего не утверждает."
    ),
    "chain_c": (
        "Mednye Truby Zvona. Документ объявляет ручной переход к четвёртому звену цепочки."
    ),
    "chain_d": (
        "Rzhany Vech Yarmarka. Конец цепочки обхода, переходов не объявляет вовсе."
    ),
}

#: Ручные связи цепочки. Объявить их в контракте загрузки НЕЛЬЗЯ, и это не дефект сценария:
#: `CommitStage` проверяет концы связи по `known_ids = set(entity_ids)` - по сущностям
#: ТОГО ЖЕ документа (orchestrator.py). Связь, у которой хотя бы один конец объявлен в другом
#: документе, отвергается с "COMMIT: link references unknown context node". Проверено на
#: прогоне: три документа из четырёх упали именно на этом.
#:
#: Значит цепочку, где каждый узел принадлежит своему документу, можно собрать только рёбрами
#: в графе. Поэтому здесь два шага: четыре документа через ingest (каждый со своим тегом, без
#: связей - этот путь проверен) и три ребра MERGE напрямую в Neo4j.
#:
#: Рёбра намеренно без `chunk_ids` и с `scope: 'user'`: это структурные пользовательские
#: утверждения, они не кандидаты на уборку и не должны ею стать.
CHAIN_EDGES: tuple[tuple[str, str], ...] = (
    ("chain_a", "chain_b"),
    ("chain_b", "chain_c"),
    ("chain_c", "chain_d"),
)

#: Вопросы и запрошенная глубина. Текст вопроса у каждого свой: одинаковый вопрос, отличающийся
#: только глубиной, упирается в семантический кэш, а на попадании в кэш глубина запроса не
#: применяется вовсе (см. CACHE_NOTE).
CHECKS: tuple[dict[str, Any], ...] = (
    {
        "id": "T9",
        "depth": 1,
        "question": "Zvonkyi Hvorist Ukladu",
        "expect_nodes": 2,
        "expect_documents": 2,
        "expect_clamped": False,
        "why": "один хоп: якорь и первое звено",
    },
    {
        "id": "T10",
        "depth": 2,
        "question": "Zvonkyi Hvorist Ukladu, вторая проверка",
        "expect_nodes": 3,
        "expect_documents": 3,
        "expect_clamped": False,
        "why": "два хопа: якорь и два звена",
    },
    {
        "id": "T11",
        "depth": 99,
        "question": "Zvonkyi Hvorist Ukladu, третья проверка",
        "expect_nodes": 4,
        "expect_documents": 4,
        "expect_clamped": True,
        "why": "просьба дальше потолка: зажим обязателен и обязан быть объявлен",
    },
    {
        "id": "T12",
        "depth": 3,
        "question": "Zvonkyi Hvorist Ukladu, четвёртая проверка",
        "expect_nodes": 4,
        "expect_documents": 4,
        "expect_clamped": False,
        "why": "та же полная глубина без зажима: тот же результат, но depth_clamped нет",
    },
)

#: Якорный вопрос должен находить документ посева. Строка дублируется в CHECKS намеренно:
#: привязка к телу документа должна быть видна в одном месте с вопросами.
ANCHOR_QUESTION = CHECKS[0]["question"]

CACHE_NOTE = (
    "На попадании в семантический кэш глубина запроса не применяется, и effective_retrieval "
    "в ответе отсутствует вовсе (pipeline.py отдаёт cached_done без него). Поэтому вопросы "
    "различаются текстом, и каждый замер обязан увидеть cache_hit=false, иначе вердикт "
    "inconclusive, а не pass"
)

EDGE_KIND = "REFERENCES"


def _required(name: str) -> str:
    """Переменная окружения без дефолта: молчаливый адрес означал бы прогон не туда."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} не задан: сценарий не угадывает адреса и домен")
    return value


def _api_key() -> str:
    """Ключ ingest-api. Принимаются оба имени: `API_KEY` в сценарии 7.2 и `AUTH_API_KEY`
    в сервисах стенда, и обёртка этого сценария повторяет окружение eval-runner, где ключ
    называется вторым. Одно имя на двоих означало бы, что сценарий не запустится ни с одной
    существующей обёрткой."""
    for name in ("API_KEY", "AUTH_API_KEY", "GRAPH_AUTH_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError("API_KEY/AUTH_API_KEY не задан: сценарий не угадывает ключ доступа")


def _domain() -> str:
    return os.environ.get("DOMAIN", "it").strip() or "it"


def _log(message: str) -> None:
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def _request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 60.0,
) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


def _ingestion(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    url = _required("INGESTION_URL").rstrip("/") + path
    status, text = _request(method, url, payload=payload, api_key=_api_key())
    if status >= 400:
        raise RuntimeError(f"{method} {path} -> {status}: {text[:400]}")
    return json.loads(text or "{}")


def cypher(statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Запрос к Neo4j по HTTP-транзакционному API.

    Отдельный драйвер не нужен: `POST /db/neo4j/tx/commit` - штатный интерфейс, и он не тянет
    за собой версию драйвера, которой в образе исполнителя нет.
    """
    url = f"{_required('NEO4J_HTTP').rstrip('/')}/db/neo4j/tx/commit"
    user, password = _required("NEO4J_USER"), _required("NEO4J_PASSWORD")
    body: dict[str, Any] = {
        "statements": [{"statement": statement, "parameters": parameters or {}}]
    }
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            parsed = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"neo4j вернул {exc.code}: {exc.read().decode()[:400]}") from exc
    errors = parsed.get("errors") or []
    if errors:
        raise RuntimeError(f"neo4j: {errors}")
    # Контракт ответа: список строк, каждая строка - список значений. `data` - список
    # словарей, у каждого `row` это СПИСОК, а не словарь, поэтому `row["merged"]` даёт
    # TypeError. Имена колонок лежат отдельно в `columns` и здесь не нужны: у каждого
    # запроса этого сценария ровно одна колонка.
    results = parsed.get("results") or []
    if not results:
        return []
    return [list(item.get("row") or []) for item in (results[0].get("data") or [])]


def ingest(node: str) -> dict[str, Any]:
    """Загрузка одного документа цепочки и ожидание терминального статуса.

    Без связей: их нельзя объявить здесь (см. CHAIN_EDGES), и лишняя связь в загрузке
    упала бы на COMMIT.
    """
    body = {
        "source_url": CHAIN_DOCS[node],
        "domain": _domain(),
        "doc_type": "md",
        "content": CHAIN_BODIES[node],
        "tags": [{"tag_id": node, "canonical_name": node}],
        "links": [],
        "metadata": {"scenario": SCENARIO},
    }
    _log(f"POST /documents {CHAIN_DOCS[node]}")
    started = _ingestion("POST", "/api/v1/ingestion/documents", body)
    job_id = str(started["job_id"])
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        job = _ingestion("GET", f"/api/v1/ingestion/jobs/{job_id}")
        if str(job.get("status")) in TERMINAL_STATUSES:
            return job
        time.sleep(3)
    raise RuntimeError(f"джоба {job_id} не завершилась за 30 минут")


#: Рёбра цепочки одним запросом. MATCH по node_id ИЛИ tag_id, потому что контекстный узел
#: хранит оба идентификатора, а схема отличается между бэкендами.
SEED_CHAIN = """
UNWIND $edges AS e
MATCH (a) WHERE a.node_id = e.from OR a.tag_id = e.from
MATCH (b) WHERE b.node_id = e.to OR b.tag_id = e.to
MERGE (a)-[r:REFERENCES]->(b)
SET r.domain = $domain, r.origin = 'user', r.scope = 'user', r.confidence = 1.0
RETURN count(r) AS merged
"""


def seed_chain() -> int:
    domain = _domain()
    edges = [
        {"from": f"tag:{domain}:{left}", "to": f"tag:{domain}:{right}"}
        for left, right in CHAIN_EDGES
    ]
    rows = cypher(SEED_CHAIN, {"edges": edges, "domain": domain})
    merged = int(rows[0][0]) if rows and rows[0] else 0
    _log(f"рёбер цепочки создано: {merged} из {len(edges)}")
    if merged != len(edges):
        raise RuntimeError(
            f"рёбер цепочки {merged}, ожидалось {len(edges)}: узлы не найдены, "
            "и обход пойдёт по пустому графу - сценарий был бы фиктивным"
        )
    return merged


def chain_document_urls() -> dict[str, str]:
    """node_id -> source_url: по этому соответствию считаются документы цепочки."""
    domain = _domain()
    return {f"tag:{domain}:{node}": CHAIN_DOCS[node] for node in CHAIN}


def build_graph_retriever() -> Any:
    """Ретрайвер графа поверх того же Neo4j, что и у конвейера.

    Обход измеряется напрямую, а не через `pipeline.run()`, и это вынужденно. Векторная
    ось на eval-стенде не даёт сидов: `context_ids` наполняются офлайн-бэкфиллом
    проекции, а маршрута бэкфилла у ingestion-api нет вообще (в app.py только
    `maintenance/orphan-cleanup`). Поэтому `pipeline.run()` не доходит до обхода вовсе:
    события `graph_expansion` в трассировке нет. Проверять глубину по пустому обходу
    бессмысленно, поэтому сид задаётся явно, а контракт параметра запроса проверяется
    отдельно, через конвейер.
    """
    from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore
    from graphrag_proto.retrieval.retrievers import GraphRetriever

    store = Neo4jGraphStore(
        _required("NEO4J_URI"),
        _required("NEO4J_USER"),
        _required("NEO4J_PASSWORD"),
    )
    return GraphRetriever(store, {"retrieval": {}}, max_nodes=32, domain=_domain())


def run_check(pipeline: Any, retriever: Any, check: dict[str, Any]) -> dict[str, Any]:
    """Один замер из двух независимых частей.

    Часть 1 - сам обход: `expand()` с явным сидом и запрошенной глубиной. Считаются узлы
    цепочки и документы-носители этих узлов (по `source_ids` самих узлов), а НЕ источники
    ответа: источники проходят через векторный поиск, rerank и отсев контекста, и на
    общем тексте посева они дают все четыре документа при любой глубине, то есть ничего
    не измеряют.

    Часть 2 - контракт параметра запроса: `pipeline.run(max_depth=...)` и
    `effective_retrieval`. Глубина оттуда не применяется (см. build_graph_retriever), но
    именно там объявляется зажим, и ради него сценарий и написан.
    """
    domain = _domain()
    seed = f"tag:{domain}:chain_a"
    by_url = {url: node for node, url in chain_document_urls().items()}
    node_document = {f"tag:{domain}:{node}": CHAIN_DOCS[node] for node in CHAIN}

    # Fanout поднят до 32 НЕМЕРНО: сценарий меряет глубину, а при дефолтных 8 извлечённые
    # связи вытесняют структурные. Обход идёт по узлу не более 8 соседей, и если у сида
    # больше восьми соседей-извлечений, ребро цепочки вытесняется ими по порядку ответа
    # Cypher. На этом стенде так и вышло: при глубине 3 вернулось 8 строк на хопе 2, все
    # мимо цепочки, а сид остался один. Это ограничение дефолта, а не глубины, и поднимать
    # его здесь - значит мерить одну ось, не подменяя её другой.
    rows = retriever.expand([seed], max_depth=check["depth"], max_fanout=32, max_nodes=64)
    walked: set[str] = {seed}
    documents: set[str] = {CHAIN_DOCS["chain_a"]}
    for row in rows:
        node_id = str(row.get("node_id") or "")
        if node_id in node_document:
            walked.add(node_id)
            for url in row.get("source_ids") or []:
                if str(url) == node_document[node_id]:
                    documents.add(str(url))
    for row in rows:
        for path in row.get("path") or []:
            if str(path) in node_document:
                walked.add(str(path))

    done = pipeline.run(
        check["question"],
        domain,
        max_depth=check["depth"],
        trace=True,
        generate=False,
    )
    effective = done.get("effective_retrieval") or {}
    trace = done.get("trace") or []
    expansion = [event for event in trace if event.get("stage") == "graph_expansion"]
# Готовность проекции - отдельное событие `graph_readiness`, и раньше сценарий его не
        # смотрел, фильтруя только `graph_expansion`. Из-за этого прогон выглядел так, будто
        # причина отключения графовой оси нигде не записывается, и диагноз был неполным:
        # причина есть, просто в другом событии.
    readiness = [event for event in trace if event.get("stage") == "graph_readiness"]
    return {
        "id": check["id"],
        "why": check["why"],
        "question": check["question"],
        "seed": seed,
        "requested_depth": check["depth"],
        "max_depth_requested": effective.get("max_depth_requested"),
        "max_depth_effective": effective.get("max_depth_effective"),
        "depth_clamped": effective.get("depth_clamped"),
        "cache_hit": bool(done.get("cache_hit")),
        "walk_seed": seed,
        "walk_max_fanout": 32,
        "walk_max_nodes": 64,
        "walk_rows": len(rows),
        "walk_hop_depths": sorted({int(row.get("depth") or 0) for row in rows}),
        "chain_nodes_walked": sorted(walked),
        "chain_node_count": len(walked),
        "chain_documents": sorted(documents),
        "chain_document_count": len(documents),
        "response_chain_documents": sorted(by_url),
        "pipeline_expansion_events": len(expansion),
        # Причина отключения графовой оси пишется в `projection_status` ответа и отдельным
        # событием `graph_readiness`. Оба сохраняются: без них прогон выглядит как «обход не
        # сработал, потому что сломалось», а не как «обход не включался, и вот почему».
        "projection_status": done.get("projection_status"),
        "graph_degraded": done.get("graph_degraded"),
        "graph_readiness": [
            {
                "status": event.get("status"),
                "degraded": event.get("degraded"),
                "projection_revision": event.get("projection_revision"),
            }
            for event in readiness
        ],
        "expect_node_count": check["expect_nodes"],
        "expect_document_count": check["expect_documents"],
        "expect_clamped": check["expect_clamped"],
    }


def _verdict(result: dict[str, Any], check: dict[str, Any]) -> tuple[str, str]:
    """Вердикт по одному замеру. `inconclusive` отдельно от `fail`."""
    if result["max_depth_requested"] is None:
        return "inconclusive", "в ответе нет effective_retrieval"
    if result["cache_hit"]:
        return "inconclusive", "попадание в семантический кэш: глубина не применялась"
    if result["max_depth_requested"] != check["depth"]:
        return "fail", "запрошенная глубина не доехала до обхода"
    if bool(result["depth_clamped"]) is not check["expect_clamped"]:
        return (
            "fail",
            f"depth_clamped={result['depth_clamped']!r}, ожидалось {check['expect_clamped']}",
        )
    if result["chain_document_count"] != check["expect_documents"]:
        return (
            "fail",
            (
                f"документов цепочки {result['chain_document_count']}, "
                f"ожидалось {check['expect_documents']}"
            ),
        )
    if result["chain_node_count"] != check["expect_nodes"]:
        return (
            "fail",
            (
                f"узлов цепочки пройдено {result['chain_node_count']}, "
                f"ожидалось {check['expect_nodes']}"
            ),
        )
    return "pass", check["why"]


def main() -> int:
    out = Path(_required("ARTIFACTS_DIR"))
    if out.exists():
        # Слияние двух прогонов в одну папку уничтожает ровно то свидетельство, ради
        # которого папка заводится, поэтому это падение, а не перезапись.
        raise RuntimeError(f"каталог артефакта уже существует: {out}")
    out.mkdir(parents=True)
    domain = _domain()

    def write(name: str, payload: Any) -> None:
        (out / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    stand = {
        "compose_files": os.environ.get("COMPOSE_FILES", ""),
        "project": os.environ.get("PROJECT", ""),
        "ingestion_url": _required("INGESTION_URL"),
        "domain": domain,
        "extract_llm": os.environ.get("EXTRACT_LLM", ""),
        "llm_model": os.environ.get("LLM_MODEL", ""),
        "query_api_present": False,
        "query_api_note": "query-api на eval-стенде отсутствует; конвейер собран через "
        "query_service.runtime.build_pipeline - тот же factory",
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    write("stand.json", stand)
    _log(f"стенд: project={stand['project']} EXTRACT_LLM={stand['extract_llm']!r}")

    seed: list[dict[str, Any]] = []
    for node in CHAIN:
        job = ingest(node)
        seed.append(
            {
                "node_id": f"tag:{domain}:{node}",
                "source_url": CHAIN_DOCS[node],
                "tag_id": node,
                "job_id": job.get("job_id"),
                "status": job.get("status"),
            }
        )
        _log(f"  {node}: {job.get('status')}")
    write("seed.json", seed)

    if any(str(item["status"]) != "succeeded" for item in seed):
        write("queries.json", [])
        write(
            "verdict.json",
            {
                "run_valid": False,
                "reason": "не все документы посева загрузились",
                "checks": {},
            },
        )
        _log("посев не удался: прогон несостоятелен")
        return 1

    # Рёбра цепочки - после посева, потому что их концы должны существовать. Через контракт
    # загрузки их объявить нельзя: концы проверяются по сущностям того же документа.
    merged = seed_chain()
    write(
        "chain.json",
        {
            "edges": [
                {
                    "from_id": f"tag:{domain}:{left}",
                    "to_id": f"tag:{domain}:{right}",
                    "kind": EDGE_KIND,
                    "origin": "user",
                    "scope": "user",
                }
                for left, right in CHAIN_EDGES
            ],
            "merged": merged,
            "note": "рёбра созданы MERGE в Neo4j, не через links контракта загрузки",
        },
    )

    from graphrag_proto.query_service.runtime import build_pipeline

    pipeline = build_pipeline(strict_profile=True)
    retriever = build_graph_retriever()
    results: dict[str, dict[str, Any]] = {}
    for check in CHECKS:
        result = run_check(pipeline, retriever, check)
        results[check["id"]] = result
        _log(
            "{id}: запрошено {requested} -> док. цепочки {count} (ждали {expect}), "
            "clamped={clamped}, cache_hit={cache}".format(
                id=result["id"],
                requested=result["requested_depth"],
                count=result["chain_document_count"],
                expect=result["expect_document_count"],
                clamped=result["depth_clamped"],
                cache=result["cache_hit"],
            )
        )
    write("queries.json", [results[check["id"]] for check in CHECKS])

    verdicts: dict[str, str] = {}
    reasons: dict[str, str] = {}
    run_valid = True
    for check in CHECKS:
        verdicts[check["id"]], reasons[check["id"]] = _verdict(results[check["id"]], check)
        if verdicts[check["id"]] == "inconclusive":
            run_valid = False

    # Пара T11/T12 держит проверку содержательной: обе должны покрыть всю цепочку, иначе
    # сравнивать нечего. Оба с clamped=true означало бы, что объявление не зависит от запроса.
    if (
        verdicts.get("T11") == "pass"
        and verdicts.get("T12") == "pass"
        and results["T11"]["chain_document_count"] != results["T12"]["chain_document_count"]
    ):
        verdicts["T12"] = "inconclusive"
        reasons["T12"] = "T11 и T12 вернули разное число документов при полном покрытии"
        run_valid = False
    if verdicts.get("T11") == "fail" and results["T11"]["depth_clamped"] is not True:
        reasons["T11"] = (
            "зажим не объявлен - это молчаливое урезание, ровно то, что сценарий и ловит"
        )

    write(
        "verdict.json",
        {
            "run_valid": run_valid,
            "checks": {
                key: {"verdict": verdicts.get(key, "not_evaluated"), "reason": reasons.get(key, "")}
                for key in ("T9", "T10", "T11", "T12")
            },
            "cache_note": CACHE_NOTE,
            "chain": list(CHAIN),
            "anchor_question": ANCHOR_QUESTION,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    for key in ("T9", "T10", "T11", "T12"):
        _log(f"  {key}: {verdicts.get(key, 'not_evaluated')} - {reasons.get(key, '')}")
    return 0 if all(verdicts.get(key) == "pass" for key in ("T9", "T10", "T11", "T12")) else 1


if __name__ == "__main__":
    sys.exit(main())
