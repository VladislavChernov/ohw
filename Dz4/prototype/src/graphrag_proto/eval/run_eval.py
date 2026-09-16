"""Eval-раннер (ADR-015): прогон baseline vs hybrid, lift-отчёт.

Прогоняет датасет через QueryPipeline (baseline: graph off, hybrid: graph on),
собирает Retrieval@K и generation metrics, фиксирует ревизию в metadata,
пишет lift-отчёт (JSON + markdown).

Запуск:
    uv run python -m graphrag_proto.eval.run_eval --domain it --mode hybrid \
        --questions path/to/questions.jsonl --out reports/
    или (integration test):
    pytest tests/test_eval_runner.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from graphrag_proto.eval.metrics import (
    generation_metrics,
    lift_report,
    retrieval_metrics,
)


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Загрузка questions.jsonl (ADR-015 формат)."""
    questions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        questions.append(json.loads(line))
    return questions


def run_eval_single(
    question: dict[str, Any],
    pipeline: Any,
    domain: str,
    revision: str | None = None,
    judge: Any | None = None,
) -> dict[str, Any]:
    """Прогон одного вопроса: pipeline.run + метрики."""
    source_urls_expected = set(question.get("golden_sources", []))
    golden_facts = question.get("golden_facts", [])

    events: list[tuple[str, dict[str, Any]]] = []
    done = pipeline.run(
        question["query"],
        domain,
        emit=lambda t, p: events.append((t, p)),
        revision=revision,
    )

    # Извлекаем source_url из sources (формат done["sources"])
    retrieved_urls: list[str] = []
    for src in (done.get("sources") or []):
        url = src.get("source_url") if isinstance(src, dict) else None
        if url:
            retrieved_urls.append(url)

    ret = retrieval_metrics(retrieved_urls, source_urls_expected, k=5)

    gen = {"groundedness": 0.0, "coverage": 0.0, "hallucination_rate": 1.0, "mode": "skipped"}
    if judge is not None and golden_facts:
        gen = generation_metrics(done.get("text", ""), golden_facts, judge)

    return {
        "id": question.get("id", ""),
        "query": question.get("query", ""),
        "retrieval": ret,
        "generation": gen,
        "revision": done.get("revision"),
    }


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Агрегация метрик по набору вопросов."""
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
            "mode": results[0]["generation"].get("mode", "unknown"),
        },
    }


def run_full_eval(
    dataset_path: Path,
    pipeline: Any,
    domain: str,
    revision: str | None = None,
    judge: Any | None = None,
) -> dict[str, Any]:
    """Полный прогон датасета и возврат агрегированных метрик."""
    questions = load_dataset(dataset_path)
    results = [run_eval_single(q, pipeline, domain, revision, judge) for q in questions]
    agg = aggregate_metrics(results)
    agg["question_count"] = len(results)
    agg["revision"] = revision
    return agg


def write_lift_report(report: dict[str, Any], out_dir: Path) -> None:
    """Запись lift-отчёта в JSON и markdown."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "lift_report.json"
    md_path = out_dir / "lift_report.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Lift Report (ADR-015)\n",
        f"**Run ID:** {report.get('run_id', 'n/a')}  ",
        f"**Created:** {report.get('created_at', 'n/a')}  ",
        f"**Revision:** {report.get('revision', 'n/a')}  ",
        f"**Verdict:** `{report.get('verdict', 'n/a')}`\n",
        "## Baseline (vector-only)\n",
    ]
    for section in ("retrieval", "generation"):
        md_lines.append(f"### {section.title()}\n")
        for k, v in report.get("baseline", {}).get(section, {}).items():
            md_lines.append(f"- **{k}**: {v}\n")

    md_lines.append("\n## Target (hybrid)\n")
    for section in ("retrieval", "generation"):
        md_lines.append(f"### {section.title()}\n")
        for k, v in report.get("target", {}).get(section, {}).items():
            md_lines.append(f"- **{k}**: {v}\n")

    md_lines.append("\n## Delta\n")
    for k, v in report.get("delta", {}).items():
        md_lines.append(f"- **{k}**: {v:+.4f}\n")

    md_path.write_text("".join(md_lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="M4 Eval Runner (ADR-015)")
    parser.add_argument("--domain", required=True, choices=["it", "library", "cinema"])
    parser.add_argument("--mode", required=True, choices=["baseline", "hybrid"])
    parser.add_argument("--questions", required=True, help="Path to questions.jsonl")
    parser.add_argument("--out", default="reports/", help="Output directory for lift report")
    args = parser.parse_args()

    from graphrag_proto.query_service.runtime import build_pipeline

    pipeline = build_pipeline()
    dataset_path = Path(args.questions)
    agg = run_full_eval(dataset_path, pipeline, args.domain, revision=None)

    report = lift_report(
        baseline=agg if args.mode == "baseline" else {"retrieval": {}, "generation": {}},
        target=agg if args.mode == "hybrid" else {"retrieval": {}, "generation": {}},
        revision=agg.get("revision"),
    )
    write_lift_report(report, Path(args.out))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()