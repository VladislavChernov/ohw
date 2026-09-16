"""Eval-раннер (ADR-015): ingest corpus → baseline vs hybrid → lift-отчёт.

Запуск:
    python infra/eval/run_eval.py --domain it --corpus docs/ --dataset infra/eval/it/questions.jsonl --out reports/
    python infra/eval/run_eval.py --domain it --mode baseline  # только vector-only
    python infra/eval/run_eval.py --domain it --mode hybrid    # graph + vector

Env:
    EVAL_LLM_ADAPTER=fake|openai — judge (defaults to fake = метрики-заглушки)
    RETRIEVAL_GRAPH_ENABLED=true|false — override graph axis
    INGESTION_URL, QUERY_URL, X_API_KEY — API endpoints
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
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
# Corpus ingestion (through Ingestion API :8002)
# ---------------------------------------------------------------------------

def ingest_corpus(corpus_dir: Path, domain: str) -> int:
    """Загрузить все .md файлы из corpus_dir в Ingestion API. Возвращает кол-во загруженных."""
    count = 0
    for md_file in sorted(corpus_dir.rglob("*.md")):
        source_url = str(md_file.relative_to(corpus_dir)).replace("\\", "/")
        content = md_file.read_text(encoding="utf-8")
        _post_json(
            f"{INGESTION_URL}/api/v1/ingestion/documents",
            {
                "source_url": source_url,
                "domain": domain,
                "doc_type": "md",
                "content": content,
            },
        )
        count += 1
    return count


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

def build_eval_pipeline(graph_enabled: bool) -> Any:
    """Собрать QueryPipeline с нужным toggling graph axis."""
    # Напрямую импортируем runtime — не ленивый, нужен для сборки
    from graphrag_proto.query_service.runtime import build_pipeline

    # Для baseline отключаем graph через RETRIEVAL_GRAPH_ENABLED
    old_val = os.environ.get("RETRIEVAL_GRAPH_ENABLED")
    try:
        os.environ["RETRIEVAL_GRAPH_ENABLED"] = "true" if graph_enabled else "false"
        pipeline = build_pipeline()
    finally:
        if old_val is None:
            os.environ.pop("RETRIEVAL_GRAPH_ENABLED", None)
        else:
            os.environ["RETRIEVAL_GRAPH_ENABLED"] = old_val
    return pipeline


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
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    questions = load_dataset(dataset_path)
    out_dir = Path(args.out)
    judge = build_judge()
    revision = fetch_revision(args.domain)

    def _run_mode(mode: str) -> dict[str, Any]:
        graph_enabled = mode == "hybrid"
        pipeline = build_eval_pipeline(graph_enabled)
        results = [eval_question(q, pipeline, args.domain, revision, judge) for q in questions]
        return aggregate(results)

    modes = ["baseline", "target"] if args.mode == "both" else [args.mode]
    reports: dict[str, Any] = {}
    for mode in modes:
        key = "baseline" if mode == "baseline" else "target"
        reports[key] = _run_mode(mode)

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
