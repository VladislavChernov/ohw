"""Eval-раннер (ADR-015): ingest corpus → baseline vs hybrid → lift-отчёт.

Запуск:
    python infra/eval/run_eval.py --domain it --corpus docs/ --dataset infra/eval/it/questions.jsonl --out reports/
    python infra/eval/run_eval.py --domain it --mode baseline  # только vector-only
    python infra/eval/run_eval.py --domain it --mode hybrid    # graph + vector

Env:
    EVAL_LLM_ADAPTER=fake|openai — judge (defaults to fake = метрики-заглушки)
    RETRIEVAL_GRAPH_ENABLED=true|false — override graph axis
    INGESTION_URL, QUERY_URL, X_API_KEY — API endpoints

Fail-fast (не тратим время на упавший стек):
    Перед прогоном раннер health-пробами проверяет доступность контуров,
    необходимых для сконфигурированных адаптеров; при падении обязательного —
    отчёт в ``<out>/preflight.log`` и выход с кодом 1.
    Все ожидания ограничены ``--wait-timeout`` (по умолчанию 300 с = 5 минут).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from graphrag_proto.eval.metrics import (
    generation_metrics,
    lift_report,
    retrieval_metrics,
)

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:8002")
QUERY_URL = os.environ.get("QUERY_URL", "http://localhost:8000")
X_API_KEY = os.environ.get("X_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")

DEFAULT_WAIT_TIMEOUT_S = 300.0
DEFAULT_CONTOUR_TIMEOUT_S = 5.0


def _api_headers() -> dict[str, str]:
    return {"X-API-Key": X_API_KEY, "Content-Type": "application/json"}


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_api_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {url}: {exc.read()[:200]!r}") from exc


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=_api_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {url}") from exc


# ---------------------------------------------------------------------------
# Preflight: fail-fast проверка доступности контуров (не ждём на упавший стек)
# ---------------------------------------------------------------------------

def _probe_http(base_url: str, timeout_s: float = DEFAULT_CONTOUR_TIMEOUT_S) -> tuple[bool, str]:
    """Health-проба HTTP-сервиса (GET /health) с коротким таймаутом."""
    url = f"{base_url.rstrip('/')}/health"
    req = urllib.request.Request(url, headers=_api_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, str(exc)


def _probe_bolt(uri: str, timeout_s: float = DEFAULT_CONTOUR_TIMEOUT_S) -> tuple[bool, str]:
    """TCP-проба bolt://host:port (Neo4j) с коротким таймаутом."""
    try:
        rest = uri.split("://", 1)[1]
    except IndexError:
        return False, f"невалидный bolt-URI: {uri!r}"
    host, _, port_raw = rest.partition(":")
    if not host:
        return False, f"нет хоста в bolt-URI: {uri!r}"
    try:
        port: int = int(port_raw) if port_raw else 7687
    except ValueError:
        return False, f"невалидный порт в bolt-URI: {uri!r}"
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True, f"TCP {host}:{port}"
    except OSError as exc:
        return False, str(exc)


def required_contours() -> list[dict[str, Any]]:
    """Контуры, нужные для сконфигурированных адаптеров (env): ingestion + backend'ы.

    Всегда требуется Ingestion API (revision fetch). Neo4j/embeddings/reranker/llm
    проверяются, только если соответствующий адаптер выбран env-переменными.
    """
    contours: list[dict[str, Any]] = [
        {"name": "ingestion-api", "kind": "http", "target": INGESTION_URL},
    ]
    if os.environ.get("GRAPH_STORE", "inmemory") == "neo4j" or os.environ.get("VECTOR_STORE", "inmemory") == "neo4j":
        contours.append({"name": "neo4j", "kind": "bolt", "target": os.environ.get("NEO4J_URI", "bolt://neo4j:7687")})
    if os.environ.get("EMBEDDER", "deterministic") == "bge_m3_service":
        contours.append({"name": "embeddings-service", "kind": "http", "target": os.environ.get("EMBEDDINGS_URL", "http://embeddings-service:8004")})
    if os.environ.get("RERANKER", "noop") == "bge_reranker":
        contours.append({"name": "reranker-service", "kind": "http", "target": os.environ.get("RERANKER_URL", "http://reranker:8006")})
    if os.environ.get("LLM_ADAPTER", "openai") == "openai":
        contours.append({"name": "llm", "kind": "http", "target": os.environ.get("LLM_BASE_URL", "http://llm:8080")})
    return contours


def preflight(out_dir: Path, timeout_s: float = DEFAULT_CONTOUR_TIMEOUT_S) -> bool:
    """Проверить доступность контуров; результаты — в лог и в reports/preflight.log.

    Возвращает True, если все обязательные контуры живы; False — прерывать прогон.
    """
    contours = required_contours()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "preflight.log"

    lines = [
        f"# Preflight (fail-fast) {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"Ingestion URL: {INGESTION_URL} | Query URL: {QUERY_URL}",
    ]
    all_ok = True
    for contour in contours:
        name, kind, target = contour["name"], contour["kind"], contour["target"]
        if kind == "http":
            ok, detail = _probe_http(target, timeout_s)
        else:
            ok, detail = _probe_bolt(target, timeout_s)
        status = "OK" if ok else "DOWN"
        print(f"preflight: [{status}] {name} ({kind}) {target} — {detail}")
        lines.append(f"{status}\t{name}\t{kind}\t{target}\t{detail}")
        all_ok = all_ok and ok

    verdict = "READY" if all_ok else "FAIL"
    lines.append(f"verdict: {verdict}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"preflight: verdict={verdict}; отчёт: {log_path}")
    return all_ok


# ---------------------------------------------------------------------------
# Corpus ingestion (through Ingestion API :8002)
# ---------------------------------------------------------------------------

def ingest_corpus(corpus_dir: Path, domain: str, source_prefix: str = "", max_wait_s: float = DEFAULT_WAIT_TIMEOUT_S) -> list[str]:
    """Загрузить все .md из corpus_dir в Ingestion API (async). Возвращает job_id'ы.

    Цикл 429 ограничен дедлайном max_wait_s: если слоты не освобождаются —
    RuntimeError вместо бесконечного ожидания упавшего стека.
    """
    job_ids: list[str] = []
    for md_file in sorted(corpus_dir.rglob("*.md")):
        rel = str(md_file.relative_to(corpus_dir)).replace("\\", "/")
        source_url = f"{source_prefix}/{rel}" if source_prefix else rel
        content = md_file.read_text(encoding="utf-8")
        body = {
            "source_url": source_url,
            "domain": domain,
            "doc_type": "md",
            "content": content,
        }
        deadline = time.monotonic() + max_wait_s
        while True:
            try:
                resp = _post_json(
                    f"{INGESTION_URL}/api/v1/ingestion/documents",
                    body,
                )
                break
            except RuntimeError as exc:
                # 429: все слоты исполнения заняты — ждём и повторяем (async-пайплайн).
                if "429" not in str(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"лимит ожидания слотов ingest ({max_wait_s:.0f}с) исчерпан для {source_url}"
                    ) from exc
                time.sleep(5.0)
        job_id = resp.get("job_id")
        if job_id:
            job_ids.append(str(job_id))
    return job_ids


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


def wait_jobs(job_ids: list[str], poll_s: float = 5.0, timeout_s: float = DEFAULT_WAIT_TIMEOUT_S) -> dict[str, str]:
    """Ждать завершения async-джобов ingestion (200 → task complete).

    Дедлайн по умолчанию — 5 минут (не 30): упавший стек не должен «выжигать» время.
    """
    deadline = time.monotonic() + timeout_s
    statuses: dict[str, str] = {}
    while time.monotonic() < deadline and len(statuses) < len(job_ids):
        for job_id in job_ids:
            if job_id in statuses:
                continue
            try:
                resp = _get_json(f"{INGESTION_URL}/api/v1/ingestion/jobs/{job_id}")
            except RuntimeError:
                continue
            status = str(resp.get("status", ""))
            if status in TERMINAL_JOB_STATUSES:
                statuses[job_id] = status
            if status == "failed":
                raise RuntimeError(f"джоба {job_id} упала: {resp.get('error')}")
        if len(statuses) < len(job_ids):
            time.sleep(poll_s)
    if len(statuses) < len(job_ids):
        pending = [job_id for job_id in job_ids if job_id not in statuses]
        raise TimeoutError(f"не дождались завершения джобов: {pending}")
    return statuses


# ---------------------------------------------------------------------------
# Revision fetch
# ---------------------------------------------------------------------------

def fetch_revision(domain: str) -> str | None:
    """GET /api/v1/ingestion/revision?domain= → revision string or None."""
    try:
        resp = _get_json(f"{INGESTION_URL}/api/v1/ingestion/revision?domain={domain}")
        rev = resp.get("revision")
        return str(rev) if rev else None
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Dataset loading (ADR-015 format)
# ---------------------------------------------------------------------------

def load_dataset(path: Path) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        questions.append(json.loads(line))
    return questions


# ---------------------------------------------------------------------------
# Pipeline builder with graph toggle
# ---------------------------------------------------------------------------

@contextmanager
def graph_toggle(graph_enabled: bool) -> Iterator[Any]:
    """Тумблер графовой оси: env живёт ВО ВРЕМЯ pipeline.run() (не при сборке).

    `graph_search_enabled(profile)` читает `RETRIEVAL_GRAPH_ENABLED` в каждом
    run() (pipeline.py:150), поэтому env должен быть выставлен на весь прогон,
    а не только на момент построения пайплайна.
    """
    old_val = os.environ.get("RETRIEVAL_GRAPH_ENABLED")
    os.environ["RETRIEVAL_GRAPH_ENABLED"] = "true" if graph_enabled else "false"
    try:
        yield
    finally:
        if old_val is None:
            os.environ.pop("RETRIEVAL_GRAPH_ENABLED", None)
        else:
            os.environ["RETRIEVAL_GRAPH_ENABLED"] = old_val


def build_eval_pipeline() -> Any:
    """Собрать QueryPipeline из env (runtime.build_pipeline)."""
    from graphrag_proto.query_service.runtime import build_pipeline

    return build_pipeline()


# ---------------------------------------------------------------------------
# Judge builder
# ---------------------------------------------------------------------------

def build_judge() -> Any | None:
    """Собрать judge из EVAL_LLM_ADAPTER=fake|openai; fake → None (метрики-заглушки)."""
    adapter_kind = os.environ.get("EVAL_LLM_ADAPTER", "fake").strip().lower()
    if adapter_kind == "fake" or not adapter_kind:
        return None
    if adapter_kind == "openai":
        from graphrag_proto.retrieval.adapters.llm import OpenAICompatibleAdapter
        return OpenAICompatibleAdapter(
            base_url=os.environ.get("LLM_BASE_URL", "http://llm:8080"),
            model=os.environ.get("LLM_MODEL", "qwen2.5-coder-7b-instruct-abliterated-q4_k_m"),
            timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "600")),
        )
    raise ValueError(f"EVAL_LLM_ADAPTER={adapter_kind!r}: допустимо fake|openai")


# ---------------------------------------------------------------------------
# Single-question eval
# ---------------------------------------------------------------------------

def eval_question(question: dict[str, Any], pipeline: Any, domain: str, revision: str | None, judge: Any | None) -> dict[str, Any]:
    golden_sources = set(question.get("golden_sources", []))
    golden_facts = question.get("golden_facts", [])

    events: list[tuple[str, dict[str, Any]]] = []
    done = pipeline.run(question["query"], domain, emit=lambda t, p: events.append((t, p)), revision=revision)

    retrieved_urls: list[str] = [
        src.get("source_url") for src in (done.get("sources") or []) if isinstance(src, dict) and src.get("source_url")
    ]
    ret = retrieval_metrics(retrieved_urls, golden_sources, k=5)

    gen: dict[str, Any] = {"groundedness": 0.0, "coverage": 0.0, "hallucination_rate": 1.0, "mode": "skipped"}
    if judge is not None and golden_facts:
        gen = generation_metrics(done.get("text", ""), golden_facts, judge)

    return {"id": question.get("id", ""), "query": question.get("query", ""), "retrieval": ret, "generation": gen, "revision": done.get("revision")}


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"retrieval": {}, "generation": {}}

    def _avg(key: str, section: str) -> float:
        vals = [r[section][key] for r in results if key in r.get(section, {})]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "retrieval": {
            "recall_at_k": _avg("recall_at_k", "retrieval"),
            "precision_at_k": _avg("precision_at_k", "retrieval"),
            "mrr_at_k": _avg("mrr_at_k", "retrieval"),
            "ndcg_at_k": _avg("ndcg_at_k", "retrieval"),
            "k": 5,
        },
        "generation": {
            "groundedness": _avg("groundedness", "generation"),
            "coverage": _avg("coverage", "generation"),
            "hallucination_rate": _avg("hallucination_rate", "generation"),
            "mode": results[0]["generation"].get("mode", "unknown") if results else "unknown",
        },
        "question_count": len(results),
    }


def write_lift_report(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "lift_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Lift Report (ADR-015)\n",
        f"**Run ID:** {report.get('run_id', 'n/a')}  ",
        f"**Created:** {report.get('created_at', 'n/a')}  ",
        f"**Verdict:** `{report.get('verdict', 'n/a')}`\n",
    ]
    for mode_name in ("baseline", "target"):
        lines.append(f"\n## {mode_name.title()}\n")
        for section in ("retrieval", "generation"):
            lines.append(f"### {section.title()}\n")
            for k, v in report.get(mode_name, {}).get(section, {}).items():
                lines.append(f"- **{k}**: {v}\n")
    lines.append("\n## Delta\n")
    for k, v in report.get("delta", {}).items():
        lines.append(f"- **{k}**: {v:+.4f}\n")
    (out_dir / "lift_report.md").write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="M4 Eval Runner (ADR-015)")
    parser.add_argument("--domain", required=True, choices=["it", "library", "cinema"])
    parser.add_argument("--mode", required=True, choices=["baseline", "hybrid", "both"])
    parser.add_argument("--corpus", help="Path to corpus dir (docs/*.md); skips ingestion if omitted")
    parser.add_argument("--dataset", required=True, help="Path to questions.jsonl")
    parser.add_argument("--out", default="reports/", help="Output directory")
    parser.add_argument("--source-prefix", default="", help="Optional prefix for source_url (e.g. docs)")
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT_S,
        help="Максимум ожидания джобов/слотов ingest, сек (по умолч. 300 = 5 мин)",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Пропустить fail-fast проверку доступности контуров",
    )
    args = parser.parse_args()

    # Единый лимит ожидания сети: если LLM_TIMEOUT_S не задан явно — 5 минут, не 600с.
    os.environ.setdefault("LLM_TIMEOUT_S", str(DEFAULT_WAIT_TIMEOUT_S))

    dataset_path = Path(args.dataset)
    questions = load_dataset(dataset_path)
    out_dir = Path(args.out)

    # 0. Preflight: fail-fast проверка контуров — упавший стек не «выжигает» время.
    if not args.no_preflight and not preflight(out_dir):
        print("preflight FAILED: обязательный контур недоступен — прогон прерван")
        raise SystemExit(1)

    judge = build_judge()

    # 1. Ingest корпуса (async) и ждём завершения — ревизия знаний до прогона.
    if args.corpus:
        corpus_dir = Path(args.corpus)
        job_ids = ingest_corpus(corpus_dir, args.domain, args.source_prefix, max_wait_s=args.wait_timeout)
        print(f"ingest: {len(job_ids)} документов поставлено, ждём завершения ...")
        statuses = wait_jobs(job_ids, timeout_s=args.wait_timeout)
        print(f"ingest: завершено ({len(statuses)}/{len(job_ids)})")
    revision = fetch_revision(args.domain)
    print(f"revision: {revision}")

    def _run_mode(mode: str) -> dict[str, Any]:
        # UC12-01 fix: в режиме ``both`` baseline выключает граф, target — включает.
        # Было: graph_enabled = mode == "hybrid"  →  в both оба режима выключали граф.
        graph_enabled = mode in ("hybrid", "target")
        pipeline = build_eval_pipeline()
        with graph_toggle(graph_enabled):
            results = [eval_question(q, pipeline, args.domain, revision, judge) for q in questions]
        return aggregate(results)

    modes = ["baseline", "target"] if args.mode == "both" else [args.mode]
    reports: dict[str, Any] = {}
    for mode in modes:
        key = "baseline" if mode == "baseline" else "target"
        print(f"run: {key} ({len(questions)} вопросов) ...")
        reports[key] = _run_mode(mode)
        print(f"run: {key} → {json.dumps(reports[key], ensure_ascii=False)}")

    report = lift_report(
        baseline=reports.get("baseline", {}),
        target=reports.get("target", {}),
        revision=revision,
    )
    report["run_id"] = f"eval_{int(time.time())}"
    report["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_lift_report(report, out_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
