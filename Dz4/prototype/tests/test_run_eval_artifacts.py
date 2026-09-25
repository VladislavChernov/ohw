"""Артефакты eval-прогона (design.md §5): манифест, qa_log, trace, пары прогонов.

Покрывает задачи 5.1–5.9 на уровне юнитов и главного потока (fake-pipeline,
без сети): вердикт n/a без судьи, mode skipped в retrieval-only, манифест
пишется один раз, trace.jsonl — только с --trace, слои 1–3 пишутся в
--no-judge/--retrieval-only, правило парности манифестов.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.eval.metrics import lift_report

EVAL_PY = Path(__file__).resolve().parent.parent / "infra" / "eval" / "run_eval.py"


def _load_run_eval() -> Any:
    spec = importlib.util.spec_from_file_location("run_eval", EVAL_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_run_eval = _load_run_eval()


GRAPH_SOURCE = "docs/02_profiling.md"
VECTOR_SOURCE = "docs/03_indexes.md"


def _question(qid: str, graph_evidence: bool = True) -> dict[str, Any]:
    return {
        "id": qid,
        "query": f"тестовый вопрос {qid}?",
        "golden_sources": [GRAPH_SOURCE, VECTOR_SOURCE],
        "golden_facts": [f"факт {qid}"] if graph_evidence else ["факт без графа"],
        "golden_graph_evidence": graph_evidence,
        "evidence_policy": "graph_required" if graph_evidence else "any",
        "reasoning_type": "multi-hop",
        "answerability": "answerable",
    }


class _FakePipeline:
    def __init__(
        self,
        trace_events: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> None:
        self._trace_events = trace_events or [
            {"stage": "embedding", "ms": 1.1, "dimensions": 8},
            {"stage": "graph", "enabled": True, "skeleton_rows": []},
            {"stage": "vector", "candidates": [{"chunk_id": "c1", "score_before": 0.5}]},
            {"stage": "done", "total_time_s": 0.3},
        ]
        self._sources = sources or [
            {"source_url": GRAPH_SOURCE, "axis": "graph", "relevance": 0.9},
        ]

    def run(
        self,
        query: str,
        domain: str | None = None,
        emit: Any = None,
        revision: str | None = None,
        generate: bool = True,
        trace: bool = False,
    ) -> dict[str, Any]:
        for event_type, payload in (("status", {"stage": "embedding"}), ("done", {})):
            if emit:
                emit(event_type, payload)
        done: dict[str, Any] = {
            "text": "сгенерированный ответ" if generate else "",
            "sources": self._sources,
            "revision": revision or "rev-fake",
            "retrieval_time_s": 0.1,
            "generation_time_s": 0.2 if generate else 0.0,
            "total_time_s": 0.3,
            "cache_hit": False,
        }
        if trace:
            done["trace"] = self._trace_events
        return done


def _write_dataset(tmp_path: Path) -> Path:
    path = tmp_path / "questions.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_question("art-01", graph_evidence=True), ensure_ascii=False) + "\n")
        fh.write(json.dumps(_question("art-02", graph_evidence=False), ensure_ascii=False) + "\n")
    return path


def _run_main(
    monkeypatch: Any,
    tmp_path: Path,
    *extra_args: str,
    mode: str = "both",
) -> None:
    dataset = _write_dataset(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "run_eval.py",
        "--domain", "it",
        "--mode", mode,
        "--dataset", str(dataset),
        "--out", str(tmp_path / "out"),
        "--no-preflight",
        *extra_args,
    ])
    monkeypatch.setattr(_run_eval, "build_eval_pipeline", lambda: _FakePipeline())
    monkeypatch.setattr(_run_eval, "fetch_revision", lambda domain: "rev-fake")
    monkeypatch.setattr(_run_eval, "code_commit", lambda: "abc1234")
    monkeypatch.setenv("LLM_ADAPTER", "fake")
    monkeypatch.setenv("EVAL_LLM_ADAPTER", "fake")
    _run_eval.main()


# --- 5.2: generation.mode без судьи / retrieval-only / judge -----------------

def test_build_judge_none_returns_none(monkeypatch: Any) -> None:
    for value in ("none", "fake", ""):
        monkeypatch.setenv("EVAL_LLM_ADAPTER", value)
        assert _run_eval.build_judge() is None


def test_code_commit_requires_host_hash(monkeypatch: Any) -> None:
    monkeypatch.delenv("RUN_CODE_COMMIT", raising=False)
    with pytest.raises(RuntimeError, match="RUN_CODE_COMMIT"):
        _run_eval.code_commit()


def test_build_judge_defaults_to_openai(monkeypatch: Any) -> None:
    monkeypatch.delenv("EVAL_LLM_ADAPTER", raising=False)
    judge = _run_eval.build_judge()
    assert judge is not None


def test_eval_question_rejects_revision_mismatch() -> None:
    class MismatchedPipeline(_FakePipeline):
        def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = super().run(*args, **kwargs)
            result["revision"] = "other-revision"
            return result

    with pytest.raises(RuntimeError, match="revision mismatch"):
        _run_eval.eval_question(
            _question("art-01"),
            MismatchedPipeline(),
            "it",
            "rev-fake",
            None,
            mode="target",
            graph_enabled=True,
            run_id="run-1",
        )


def test_eval_question_mode_skipped_when_not_generate() -> None:
    record = _run_eval.eval_question(
        _question("art-01"),
        _FakePipeline(),
        "it",
        "rev-fake",
        None,
        mode="baseline",
        graph_enabled=False,
        run_id="run-1",
        generate=False,
    )
    assert record["generation"]["mode"] == "skipped"
    assert record["answer"] == ""
    assert record["revision"] == "rev-fake"


def test_eval_question_mode_n_a_without_judge() -> None:
    record = _run_eval.eval_question(
        _question("art-01"),
        _FakePipeline(),
        "it",
        "rev-fake",
        None,
        mode="baseline",
        graph_enabled=False,
        run_id="run-1",
    )
    assert record["generation"]["mode"] == "n/a"
    assert record["retrieval"]["recall_at_k"] == 0.5  # 1 их 2 golden источников
    assert record["graph_contribution"]["mode"] == "measured"


def test_eval_question_cross_axis_duplicate_caps_recall() -> None:
    """BUG-фикс: один URL на обеих осях не завышает Recall/nDCG выше 1.0."""
    q = _question("art-01")
    q["golden_sources"] = [GRAPH_SOURCE]
    dup_sources = [
        {"source_url": GRAPH_SOURCE, "axis": "graph", "relevance": 1.0},
        {"source_url": GRAPH_SOURCE, "axis": "vector", "relevance": 0.8},
    ]
    record = _run_eval.eval_question(
        q,
        _FakePipeline(sources=dup_sources),
        "it",
        "rev-fake",
        None,
        mode="target",
        graph_enabled=True,
        run_id="run-1",
        generate=False,
    )
    ret = record["retrieval"]
    assert ret["recall_at_k"] == 1.0  # было 2.0 (дубль считался дважды)
    assert ret["precision_at_k"] <= 1.0
    assert ret["ndcg_at_k"] == 1.0  # было 1.6309
    assert len(record["retrieved"]) == 2  # qa_log хранит источники с осями как есть


def test_eval_question_records_projection_state() -> None:
    class ProjectionPipeline(_FakePipeline):
        def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = super().run(*args, **kwargs)
            result["projection_status"] = "ready"
            result["projection_revision"] = "projection-1"
            result["trace"] = [
                *self._trace_events,
                {
                    "stage": "graph_expansion",
                    "seed_chunk_ids": ["c1"],
                    "paths": [["tag:it:a", "tag:it:b"]],
                    "depths": [1],
                    "boost": 0.2,
                },
            ]
            return result

    record = _run_eval.eval_question(
        _question("projection-01"),
        ProjectionPipeline(),
        "it",
        "rev-fake",
        None,
        mode="target",
        graph_enabled=True,
        run_id="run-1",
    )

    assert record["projection_status"] == "ready"
    assert record["projection_revision"] == "projection-1"
    assert record["seed_chunk_ids"] == ["c1"]
    assert record["graph_paths"] == [["tag:it:a", "tag:it:b"]]
    assert record["graph_depths"] == [1]
    assert record["graph_boost"] == 0.2


def test_eval_question_does_not_count_degraded_graph_as_measured() -> None:
    class DegradedPipeline(_FakePipeline):
        def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = super().run(*args, **kwargs)
            result["graph_degraded"] = True
            return result

    record = _run_eval.eval_question(
        _question("degraded-01"),
        DegradedPipeline(),
        "it",
        "rev-fake",
        None,
        mode="target",
        graph_enabled=True,
        run_id="run-1",
    )

    assert record["graph_degraded"] is True
    assert record["graph_contribution"]["mode"] == "degraded"
    aggregate = _run_eval.aggregate([record])
    assert aggregate["graph_contribution"]["mode"] == "not_measured"
    assert aggregate["graph_contribution"]["degraded_questions"] == 1


def test_aggregate_generation_none_without_judge() -> None:
    """n/a-режим: groundedness/coverage/hallucination_rate агрегируются как None."""
    agg = _run_eval.aggregate(
        [
            {
                "retrieval": {"recall_at_k": 0.5, "precision_at_k": 0.2, "mrr_at_k": 0.5, "ndcg_at_k": 0.5},
                "generation": {"groundedness": None, "coverage": None, "hallucination_rate": None, "mode": "n/a"},
                "graph_contribution": {
                    "mode": "measured",
                    "necessity": 0.5,
                    "delta_recall": 0.5,
                    "recall_graph": 0.5,
                    "recall_vector": 0.5,
                    "evidence_recall_graph": 0.5,
                },
            }
        ]
    )
    assert agg["generation"]["groundedness"] is None
    assert agg["generation"]["coverage"] is None
    assert agg["generation"]["hallucination_rate"] is None
    assert agg["graph_contribution"]["mode"] == "measured"
    assert "question_count" in agg


def test_eval_question_trace_events_only_when_enabled() -> None:
    record = _run_eval.eval_question(
        _question("art-01"),
        _FakePipeline(),
        "it",
        "rev-fake",
        None,
        mode="baseline",
        graph_enabled=False,
        run_id="run-1",
        trace_enabled=True,
    )
    stages = [event["stage"] for event in record["trace_events"]]
    assert stages[0] == "embedding"
    assert "graph" in stages
    assert "vector" in stages
    assert record["retrieved"][0]["axis"] == "graph"


# --- 5.1: манифест условий прогона -----------------------------------------

def test_build_run_manifest_fields(monkeypatch: Any) -> None:
    monkeypatch.setenv("RUN_CODE_COMMIT", "c0ffee")
    monkeypatch.setenv("PROJECTION_CONFIG_FINGERPRINT", "projection-config-1")
    monkeypatch.delenv("EMBEDDER", raising=False)
    manifest = _run_eval.build_run_manifest(
        run_id="run-1",
        started_at="2026-01-01T00:00:00Z",
        domain="it",
        revision="rev-fake",
        datasets=["questions.jsonl"],
        mode_arg="both",
        corpus="/repo/docs",
        source_prefix="docs",
        k=5,
        judge=None,
        flags=["--no-judge"],
    )
    assert manifest["code_commit"] == "c0ffee"
    assert manifest["corpus"] == "/repo/docs"
    assert manifest["source_prefix"] == "docs"
    assert manifest["revision_fingerprint"] == "rev-fake"
    assert manifest["graph_enabled"] == [False, True]
    assert manifest["projection"]["config_fingerprint"] == "projection-config-1"
    assert manifest["components"]["dimensions"] == 8
    assert manifest["components"]["embedder"] == "deterministic"
    assert manifest["generation"]["judge_adapter"] == "none"
    assert manifest["flags"] == ["--no-judge"]


def test_manifest_graph_enabled_per_mode() -> None:
    assert _run_eval.mode_graph_enabled("baseline") == [False]
    assert _run_eval.mode_graph_enabled("hybrid") == [True]
    assert _run_eval.mode_graph_enabled("both") == [False, True]


# --- 5.1: правило парности манифестов ---------------------------------------

def test_fetch_revision_fails_closed_on_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(_run_eval, "_get_json", lambda _url: {"revision": None})
    try:
        _run_eval.fetch_revision("it")
    except RuntimeError as exc:
        assert "revision" in str(exc)
    else:
        raise AssertionError("empty revision must stop the run")


def test_fetch_revision_rejects_non_hex(monkeypatch: Any) -> None:
    monkeypatch.setattr(_run_eval, "_get_json", lambda _url: {"revision": "not-a-sha"})
    with pytest.raises(RuntimeError, match="некорректна"):
        _run_eval.fetch_revision("it")


def test_fetch_revision_rejects_non_mapping(monkeypatch: Any) -> None:
    monkeypatch.setattr(_run_eval, "_get_json", lambda _url: [])
    with pytest.raises(RuntimeError, match="некорректна"):
        _run_eval.fetch_revision("it")


def test_compare_run_manifests_paired_and_unpaired() -> None:
    base = {
        "code_commit": "abc",
        "code_tree": "clean",
        "domain": "it",
        "revision": "rev-a",
        "revision_fingerprint": "rev-a",
        "datasets": ["questions.jsonl"],
        "corpus": "/repo/docs",
        "corpus_documents": ["docs/a.md"],
        "corpus_limit": None,
        "source_prefix": "docs",
        "k": 5,
        "components": {"embedder": "deterministic"},
        "generation": {"judge_adapter": "none"},
        "flags": ["--no-judge"],
        "mode": "both",
        "graph_enabled": [False, True],
    }
    other_same = dict(base)
    assert _run_eval.compare_run_manifests(base, other_same)["paired"] is True

    other_one_diff = dict(base, mode="hybrid")
    assert _run_eval.compare_run_manifests(base, other_one_diff)["paired"] is True

    other_revision = dict(base, revision="rev-b", revision_fingerprint="rev-b")
    pair = _run_eval.compare_run_manifests(base, other_revision)
    assert pair["paired"] is False
    assert pair["differing_invariants"] == ["revision", "revision_fingerprint"]

    for field, value in (
        ("datasets", ["other.jsonl"]),
        ("components", {"embedder": "other"}),
        ("generation", {"judge_adapter": "openai"}),
    ):
        candidate = dict(base)
        candidate[field] = value
        pair = _run_eval.compare_run_manifests(base, candidate)
        assert pair["paired"] is False
        assert field in pair["differing_invariants"]

    other_mode = dict(base, flags=["--retrieval-only"])
    pair = _run_eval.compare_run_manifests(base, other_mode)
    assert pair["paired"] is False
    assert "flags" in pair["differing_invariants"]

    incomplete = {"revision": "rev-a"}
    pair = _run_eval.compare_run_manifests(incomplete, incomplete)
    assert pair["paired"] is False
    assert "mode" in pair["missing_fields"]

    other_two_diff = dict(base, mode="hybrid", graph_enabled=[True])
    pair = _run_eval.compare_run_manifests(base, other_two_diff)
    assert pair["paired"] is False
    assert set(pair["differing_factors"]) == {"mode", "graph_enabled"}


def test_compare_run_manifests_ignores_projection_observation() -> None:
    base = {
        "code_commit": "abc",
        "code_tree": "clean",
        "domain": "it",
        "revision": "rev-a",
        "revision_fingerprint": "rev-a",
        "datasets": ["questions.jsonl"],
        "corpus": None,
        "corpus_documents": [],
        "corpus_limit": None,
        "source_prefix": "",
        "k": 5,
        "components": {},
        "generation": {},
        "flags": [],
        "mode": "both",
        "graph_enabled": [False, True],
        "projection": {
            "config_fingerprint": "cfg-1",
            "state_db": "runtime/projection.db",
            "projection_observed": {"statuses": ["ready"]},
        },
    }
    other = dict(base)
    other["projection"] = {
        **base["projection"],
        "projection_observed": {"statuses": ["stale"]},
    }
    assert _run_eval.compare_run_manifests(base, other)["paired"] is True

    other["projection"] = {**base["projection"], "config_fingerprint": "cfg-2"}
    pair = _run_eval.compare_run_manifests(base, other)
    assert pair["paired"] is False
    assert "projection" in pair["differing_invariants"]


def test_lift_report_verdict_n_a_without_judge() -> None:
    baseline = {"retrieval": {"recall_at_k": 0.5}, "generation": {}, "graph_contribution": {}}
    target = {"retrieval": {"recall_at_k": 0.8}, "generation": {}, "graph_contribution": {}}
    report = lift_report(baseline, target, judge_active=False)
    assert report["verdict"] == "n/a"


def test_ingest_corpus_rejects_response_without_job_id(monkeypatch: Any, tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "doc.md").write_text("text", encoding="utf-8")
    monkeypatch.setattr(_run_eval, "_post_json", lambda _url, _body: {})
    with pytest.raises(RuntimeError, match="job_id"):
        _run_eval.ingest_corpus(source, "it")


def test_wait_jobs_rejects_cancelled_terminal_status(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        _run_eval,
        "_get_json",
        lambda _url: {"status": "cancelled"},
    )
    with pytest.raises(RuntimeError, match="cancelled"):
        _run_eval.wait_jobs(["job"], poll_s=0, timeout_s=1)


# --- 5.3/5.6: retrieval-only + preflight-контуры -----------------------------

def test_required_contours_include_generation_toggle(monkeypatch: Any) -> None:
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    monkeypatch.delenv("VECTOR_STORE", raising=False)
    monkeypatch.setenv("LLM_ADAPTER", "openai")
    assert "llm" in {c["name"] for c in _run_eval.required_contours(include_generation=True)}
    assert "llm" not in {c["name"] for c in _run_eval.required_contours(include_generation=False)}


def test_main_rejects_empty_dataset(monkeypatch: Any, tmp_path: Path) -> None:
    dataset = tmp_path / "empty.jsonl"
    dataset.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "run_eval.py",
        "--domain", "it",
        "--mode", "both",
        "--dataset", str(dataset),
        "--out", str(tmp_path / "out"),
        "--no-preflight",
    ])
    monkeypatch.setattr(_run_eval, "build_eval_pipeline", lambda: _FakePipeline())
    monkeypatch.setattr(_run_eval, "fetch_revision", lambda domain: "rev-fake")
    monkeypatch.setenv("RUN_CODE_COMMIT", "host-hash")
    with pytest.raises(ValueError, match="empty"):
        _run_eval.main()


def test_main_retrieval_only_mode_skipped(monkeypatch: Any, tmp_path: Path) -> None:
    _run_main(monkeypatch, tmp_path, "--retrieval-only")
    out = tmp_path / "out"
    lines = [
        json.loads(line)
        for line in (out / "qa_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(lines) == 4  # 2 вопроса x 2 режима
    assert {l["generation"]["mode"] for l in lines} == {"skipped"}
    assert all(l["answer"] == "" for l in lines)
    report = json.loads((out / "lift_report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "n/a"
    assert not (out / "trace.jsonl").exists()
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["flags"] == ["--retrieval-only"]
    assert manifest["generation"]["judge_adapter"] == "none"


def test_main_no_judge_mode_n_a(monkeypatch: Any, tmp_path: Path) -> None:
    _run_main(monkeypatch, tmp_path, "--no-judge")
    out = tmp_path / "out"
    lines = [
        json.loads(line)
        for line in (out / "qa_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {l["generation"]["mode"] for l in lines} == {"n/a"}
    report = json.loads((out / "lift_report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "n/a"
    assert not (out / "trace.jsonl").exists()


# --- 5.4/5.7: trace.jsonl — только с флагом ---------------------------------

def test_main_trace_written_only_with_flag(monkeypatch: Any, tmp_path: Path) -> None:
    _run_main(monkeypatch, tmp_path, "--trace")
    out = tmp_path / "out"
    trace_lines = [
        json.loads(line)
        for line in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(trace_lines) == 4
    assert all("events" in line and line["events"][0]["stage"] == "embedding" for line in trace_lines)

    qa_lines = [
        json.loads(line)
        for line in (out / "qa_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all("trace_events" not in line for line in qa_lines)


# --- 5.6: манифест пишется один раз -----------------------------------------

def test_manifest_written_once(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[list[Any]] = []
    real_write = _run_eval.write_run_manifest

    def counting(report: dict[str, Any], out_dir: Path) -> None:
        calls.append([report, out_dir])
        real_write(report, out_dir)

    monkeypatch.setattr(_run_eval, "write_run_manifest", counting)
    _run_main(monkeypatch, tmp_path)
    assert len(calls) == 1  # манифест пишется один раз, не на каждый вопрос/режим
    manifest = json.loads((tmp_path / "out" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "both"
    assert manifest["k"] == 5
    assert manifest["domain"] == "it"


# --- 5.8/5.9: --compare-with и недействительность вердикта --------------------

def test_main_compare_with_load_failure_invalid(monkeypatch: Any, tmp_path: Path) -> None:
    """Манифест пары не прочитан → парность не подтверждена → verdict invalid."""
    _run_main(monkeypatch, tmp_path, "--compare-with", str(tmp_path / "missing.json"))
    report = json.loads((tmp_path / "out" / "lift_report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "invalid"
    assert report["pair"]["paired"] is False
    md = (tmp_path / "out" / "lift_report.md").read_text(encoding="utf-8")
    assert "прогоны не парные" in md


def test_main_trace_removed_on_rerun_without_flag(monkeypatch: Any, tmp_path: Path) -> None:
    """Устаревший trace.jsonl не остаётся после прогона без --trace."""
    _run_main(monkeypatch, tmp_path, "--trace")
    assert (tmp_path / "out" / "trace.jsonl").exists()
    _run_main(monkeypatch, tmp_path)
    assert not (tmp_path / "out" / "trace.jsonl").exists()


def test_main_compare_with_invalid_pair(monkeypatch: Any, tmp_path: Path) -> None:
    _run_main(monkeypatch, tmp_path, "--compare-with", str(tmp_path / "other.json"))
    other = {
        "mode": "hybrid",
        "graph_enabled": [True],
        "run_id": "eval_other",
        "started_at": "2026-01-01T00:00:00Z",
        "code_commit": "abc1234",
        "domain": "it",
        "revision": "rev-fake",
        "revision_fingerprint": "rev-fake",
        "datasets": ["other.jsonl"],
        "k": 5,
        "components": {},
        "generation": {},
        "flags": [],
    }
    (tmp_path / "other.json").write_text(json.dumps(other), encoding="utf-8")

    # перезапуск после создания манифеста сравнения
    _run_main(monkeypatch, tmp_path, "--compare-with", str(tmp_path / "other.json"))
    report = json.loads((tmp_path / "out" / "lift_report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "invalid"
    assert report["pair"]["paired"] is False
    md = (tmp_path / "out" / "lift_report.md").read_text(encoding="utf-8")
    assert "прогоны не парные" in md