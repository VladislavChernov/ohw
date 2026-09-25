"""Наблюдаемость eval-прогона: паспорт, ingest_report, failures, покрытие корпуса.

Покрывает доработки LP-13: усечённый корпус (3 документа) обязан быть
отличим от полного и не давать ложно-чистый результат. Поэтому проверяем:
  * ``--documents/--limit-docs`` — явный выбор корпуса и защиту от выхода за него;
  * ``golden_coverage`` — доля вопросов, чьи эталонные источники есть в корпусе;
  * ``graph_axis_active`` — графическая ось фактически сработала;
  * ``ingest_report.json`` — попер-документное время и no-op-признак;
  * ``failures.jsonl`` — ошибка вопроса не убивает прогон, но revision mismatch валит;
  * ``PASSPORT.md``/``command.txt``/``qa_review.md`` — паспорт пишется всегда;
  * инварианты парности — разный корпус ⇒ прогоны не парные.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

EVAL_PY = Path(__file__).resolve().parent.parent / "infra" / "eval" / "run_eval.py"


def _load_run_eval() -> Any:
    spec = importlib.util.spec_from_file_location("run_eval_observability", EVAL_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_run_eval = _load_run_eval()

GRAPH_SOURCE = "docs/02_profiling.md"
VECTOR_SOURCE = "docs/03_indexes.md"


def _question(qid: str, golden: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": qid,
        "query": f"вопрос {qid}?",
        "golden_sources": golden if golden is not None else [GRAPH_SOURCE, VECTOR_SOURCE],
        "golden_facts": [f"факт {qid}"],
        "golden_graph_evidence": True,
        "evidence_policy": "graph_required",
        "reasoning_type": "multi-hop",
        "answerability": "answerable",
    }


class _FakePipeline:
    def __init__(self, sources: list[dict[str, Any]] | None = None) -> None:
        self._sources = sources if sources is not None else [
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
        if emit:
            emit("done", {})
        return {
            "text": "ответ" if generate else "",
            "sources": self._sources,
            "revision": revision or "rev-fake",
            "projection_status": "ready",
            "projection_revision": "proj-1",
            "graph_degraded": False,
            "retrieval_time_s": 0.1,
            "generation_time_s": 0.0,
            "total_time_s": 0.1,
        }


class _BoomPipeline:
    """Падает RuntimeError на каждом вопросе (кроме opt-out через revision)."""

    def run(
        self,
        query: str,
        domain: str | None = None,
        emit: Any = None,
        revision: str | None = None,
        generate: bool = True,
        trace: bool = False,
    ) -> dict[str, Any]:
        raise RuntimeError("boom: адаптер упал")


def _corpus(tmp_path: Path, names: list[str]) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in names:
        (corpus / name).write_text(f"# {name}\nсодержимое {name}\n", encoding="utf-8")
    return corpus


def _write_dataset(tmp_path: Path, questions: list[dict[str, Any]] | None = None) -> Path:
    path = tmp_path / "questions.jsonl"
    rows = questions if questions is not None else [_question("obs-01")]
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    return path


# --- выбор корпуса ---------------------------------------------------------


def test_select_corpus_files_defaults_to_all_sorted(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, ["b.md", "a.md", "c.md"])

    selected = _run_eval.select_corpus_files(corpus)

    assert [rel for _path, rel in selected] == ["a.md", "b.md", "c.md"]


def test_select_corpus_files_limit_takes_first_n(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, ["a.md", "b.md", "c.md"])

    selected = _run_eval.select_corpus_files(corpus, limit=2)

    assert [rel for _path, rel in selected] == ["a.md", "b.md"]


def test_select_corpus_files_explicit_documents(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, ["a.md", "b.md", "c.md"])

    selected = _run_eval.select_corpus_files(corpus, documents=["c.md", "a.md"])

    assert sorted(rel for _path, rel in selected) == ["a.md", "c.md"]


def test_select_corpus_files_rejects_escape_outside_corpus(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, ["a.md"])
    (tmp_path / "secret.md").write_text("секрет", encoding="utf-8")

    with pytest.raises(ValueError, match="вне корпуса"):
        _run_eval.select_corpus_files(corpus, documents=["../secret.md"])


def test_select_corpus_files_rejects_missing_document(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, ["a.md"])

    with pytest.raises(FileNotFoundError):
        _run_eval.select_corpus_files(corpus, documents=["nope.md"])


def test_select_corpus_files_rejects_non_positive_limit(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, ["a.md"])

    with pytest.raises(ValueError, match="положительным"):
        _run_eval.select_corpus_files(corpus, limit=0)


# --- golden_coverage -------------------------------------------------------


def test_golden_coverage_counts_fully_and_partially() -> None:
    questions = [
        _question("q1", [GRAPH_SOURCE]),            # есть в корпусе
        _question("q2", [VECTOR_SOURCE]),          # есть в корпусе
        _question("q3", ["docs/other.md"]),         # нет в корпусе
        _question("q4", [GRAPH_SOURCE, "docs/x.md"]),  # наполовину
    ]

    coverage = _run_eval.golden_coverage(questions, {GRAPH_SOURCE, VECTOR_SOURCE})

    assert coverage["questions_total"] == 4
    assert coverage["fully_covered"] == 2
    assert coverage["partially_covered"] == 1
    assert coverage["uncovered"] == 1
    assert coverage["fully_covered_ratio"] == 0.5


def test_golden_coverage_empty_dataset_is_null_ratio() -> None:
    coverage = _run_eval.golden_coverage([], set())

    assert coverage["questions_total"] == 0
    assert coverage["fully_covered_ratio"] is None


# --- graph_axis_active -----------------------------------------------------


def _gc(**overrides: Any) -> dict[str, Any]:
    base = {
        "mode": "measured",
        "necessity": 0.5,
        "delta_recall": 0.25,
        "recall_graph": 0.75,
        "recall_vector": 0.5,
        "evidence_recall_graph": 0.75,
    }
    base.update(overrides)
    return base


def _record(sources: list[dict[str, Any]], gc: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "retrieved": sources,
        "generation": {"mode": "n/a", "groundedness": None, "coverage": None},
        "graph_contribution": gc if gc is not None else _gc(),
        "retrieval": {
            "recall_at_k": 1.0,
            "precision_at_k": 0.4,
            "mrr_at_k": 1.0,
            "ndcg_at_k": 1.0,
        },
    }


def test_aggregate_flags_graph_axis_active() -> None:
    aggregated = _run_eval.aggregate(
        [
            _record(
                [
                    {"source_url": GRAPH_SOURCE, "axis": "graph"},
                    {"source_url": VECTOR_SOURCE, "axis": "vector"},
                ]
            )
        ]
    )

    assert aggregated["graph_axis_active"] is True
    assert aggregated["graph_axis_sources"] == 1


def test_aggregate_flags_graph_axis_inactive_when_only_vector() -> None:
    aggregated = _run_eval.aggregate(
        [_record([{"source_url": VECTOR_SOURCE, "axis": "vector"}])]
    )

    assert aggregated["graph_axis_active"] is False
    assert aggregated["graph_axis_sources"] == 0


# --- ingest_report ---------------------------------------------------------


def test_build_ingest_report_detects_noop_by_stage() -> None:
    submitted = [
        {"source_url": "docs/a.md", "relpath": "a.md", "bytes": 2048, "job_id": "j1", "waited_s": 1.5},
        {"source_url": "docs/b.md", "relpath": "b.md", "bytes": 4096, "job_id": "j2", "waited_s": 120.0},
    ]
    statuses = {
        # stage=INGEST при succeeded ⇒ пайплайн встал после INGEST (документ не менялся)
        "j1": {"status": "succeeded", "stage": "INGEST", "error": None},
        "j2": {"status": "succeeded", "stage": "COMMIT", "error": None},
    }

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["documents_total"] == 2
    assert report["noop_documents"] == 1
    assert report["cold_documents"] == 1
    assert report["cold_wall_time_sum_s"] == 120.0
    assert report["cold_wall_time_mean_s"] == 120.0
    assert report["documents"][0]["noop"] is True
    assert report["documents"][0]["seconds_per_kb"] is None
    assert report["documents"][1]["noop"] is False
    assert report["documents"][1]["seconds_per_kb"] == 30.0


def test_build_ingest_report_handles_unknown_job() -> None:
    submitted = [
        {"source_url": "docs/a.md", "relpath": "a.md", "bytes": 100, "job_id": "missing", "waited_s": 2.0},
    ]

    report = _run_eval.build_ingest_report(submitted, {})

    assert report["documents"][0]["status"] == "unknown"
    assert report["cold_documents"] == 1


# --- манифест и парность ----------------------------------------------------


def _manifest(**overrides: Any) -> dict[str, Any]:
    base = {
        "run_id": "eval_a",
        "started_at": "2026-01-01T00:00:00Z",
        "code_commit": "abc1234",
        "code_tree": "dirty",
        "domain": "it",
        "revision": "rev-fake",
        "revision_fingerprint": "rev-fake",
        "datasets": ["q.jsonl"],
        "corpus": "/repo/docs",
        "corpus_documents": ["docs/a.md"],
        "corpus_limit": 3,
        "source_prefix": "docs",
        "k": 5,
        "mode": "both",
        "graph_enabled": [False, True],
        "components": {},
        "projection": {"config_fingerprint": "default", "state_db": "s.db", "lease_seconds": 300},
        "generation": {},
        "flags": [],
    }
    base.update(overrides)
    return base


def test_pair_invariance_rejects_different_corpus() -> None:
    a = _manifest()
    b = _manifest(run_id="eval_b", corpus_documents=["docs/a.md", "docs/b.md"], corpus_limit=None)

    result = _run_eval.compare_run_manifests(a, b)

    assert result["paired"] is False
    assert "corpus_documents" in result["differing_invariants"]


def test_pair_invariance_rejects_different_corpus_limit() -> None:
    a = _manifest()
    b = _manifest(run_id="eval_b", corpus_limit=8)

    result = _run_eval.compare_run_manifests(a, b)

    assert result["paired"] is False
    assert "corpus_limit" in result["differing_invariants"]


def test_pair_invariance_rejects_dirty_tree_difference() -> None:
    a = _manifest(code_tree="clean")
    b = _manifest(run_id="eval_b", code_tree="dirty:1234")

    result = _run_eval.compare_run_manifests(a, b)

    assert result["paired"] is False
    assert "code_tree" in result["differing_invariants"]


def test_pair_invariance_accepts_identical_conditions() -> None:
    a = _manifest()
    b = _manifest(run_id="eval_b")

    assert _run_eval.compare_run_manifests(a, b)["paired"] is True


def test_manifest_records_golden_coverage_and_tree_state(monkeypatch: Any) -> None:
    monkeypatch.setattr(_run_eval, "code_commit", lambda: "abc1234")
    monkeypatch.setenv("RUN_CODE_TREE", "dirty:deadbee")
    monkeypatch.delenv("PROJECTION_LEASE_SECONDS", raising=False)
    monkeypatch.setenv("PROJECTION_CONFIG_PATH", "does-not-exist.yaml")
    monkeypatch.delenv("TOPOLOGY_URL", raising=False)

    manifest = _run_eval.build_run_manifest(
        run_id="eval_x",
        started_at="2026-01-01T00:00:00Z",
        domain="it",
        revision="rev-fake",
        datasets=["q.jsonl"],
        mode_arg="both",
        corpus="/repo/docs",
        k=5,
        judge=None,
        flags=[],
        corpus_documents=["docs/a.md", "docs/b.md"],
        corpus_limit=3,
        golden={"questions_total": 10, "fully_covered": 2, "fully_covered_ratio": 0.2},
        note="smoke на 3 документах",
    )

    assert manifest["code_tree"] == "dirty:deadbee"
    assert manifest["corpus_documents"] == ["docs/a.md", "docs/b.md"]
    assert manifest["corpus_documents_count"] == 2
    assert manifest["corpus_limit"] == 3
    assert manifest["golden_coverage"]["fully_covered_ratio"] == 0.2
    assert manifest["note"] == "smoke на 3 документах"
    assert manifest["projection"]["lease_seconds"] == 300
    assert manifest["projection"]["lease_source"] == "config_file"


def test_projection_policy_env_override_recorded(monkeypatch: Any) -> None:
    monkeypatch.setenv("PROJECTION_CONFIG_PATH", "does-not-exist.yaml")
    monkeypatch.delenv("TOPOLOGY_URL", raising=False)
    monkeypatch.setenv("PROJECTION_LEASE_SECONDS", "120")

    snapshot = _run_eval.projection_policy_snapshot()

    assert snapshot["lease_seconds"] == 120
    assert snapshot["lease_source"] == "env"


def test_projection_policy_never_breaks_manifest(monkeypatch: Any) -> None:
    monkeypatch.setenv("PROJECTION_CONFIG_PATH", "does-not-exist.yaml")
    monkeypatch.setenv("PROJECTION_LEASE_SECONDS", "not-a-number")

    snapshot = _run_eval.projection_policy_snapshot()

    assert snapshot["lease_seconds"] is None
    assert "lease_error" in snapshot


# --- сквозной поток main() -------------------------------------------------


def _run_main(
    monkeypatch: Any,
    tmp_path: Path,
    *extra_args: str,
    pipeline_factory: Any = None,
    dataset: Path | None = None,
    mode: str = "both",
) -> Path:
    dataset_path = dataset if dataset is not None else _write_dataset(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "--domain", "it",
            "--mode", mode,
            "--dataset", str(dataset_path),
            "--out", str(tmp_path / "out"),
            "--no-preflight",
            *extra_args,
        ],
    )
    factory = pipeline_factory or (lambda: _FakePipeline())
    monkeypatch.setattr(_run_eval, "build_eval_pipeline", factory)
    monkeypatch.setattr(_run_eval, "fetch_revision", lambda domain: "rev-fake")
    monkeypatch.setattr(_run_eval, "code_commit", lambda: "abc1234")
    monkeypatch.setenv("LLM_ADAPTER", "fake")
    monkeypatch.setenv("EVAL_LLM_ADAPTER", "fake")
    _run_eval.main()
    return tmp_path / "out"


def test_main_writes_passport_command_and_qa_review(monkeypatch: Any, tmp_path: Path) -> None:
    out = _run_main(monkeypatch, tmp_path, "--note", "smoke: 3 документа")

    passport = (out / "PASSPORT.md").read_text(encoding="utf-8")
    assert "smoke: 3 документа" in passport
    assert "graph_axis_active" in passport
    assert "Что меряем" in passport
    assert "Воспроизведение" in passport

    command = (out / "command.txt").read_text(encoding="utf-8")
    assert "run_eval.py" in command
    assert "--note" in command

    review = (out / "qa_review.md").read_text(encoding="utf-8")
    assert GRAPH_SOURCE in review
    assert "projection" in review


def test_main_writes_empty_failures_file_on_clean_run(
    monkeypatch: Any, tmp_path: Path
) -> None:
    out = _run_main(monkeypatch, tmp_path)

    assert (out / "failures.jsonl").exists()
    assert (out / "failures.jsonl").read_text(encoding="utf-8") == ""


def test_main_records_failed_question_and_completes(
    monkeypatch: Any, tmp_path: Path
) -> None:
    dataset = _write_dataset(tmp_path, [_question("obs-01"), _question("obs-02")])

    out = _run_main(
        monkeypatch,
        tmp_path,
        pipeline_factory=lambda: _BoomPipeline(),
        dataset=dataset,
    )

    failures = [
        json.loads(line)
        for line in (out / "failures.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # по 2 вопроса × 2 ветки = 4 падения, прогон при этом завершился
    assert len(failures) == 4
    assert {row["id"] for row in failures} == {"obs-01", "obs-02"}
    assert all(row["error_type"] == "RuntimeError" for row in failures)
    assert (out / "PASSPORT.md").exists()
    report = json.loads((out / "lift_report.json").read_text(encoding="utf-8"))
    assert report["baseline"]["question_count"] == 0


def test_main_revision_mismatch_still_aborts(monkeypatch: Any, tmp_path: Path) -> None:
    class _WrongRevisionPipeline(_BoomPipeline):
        def run(
            self,
            query: str,
            domain: str | None = None,
            emit: Any = None,
            revision: str | None = None,
            generate: bool = True,
            trace: bool = False,
        ) -> dict[str, Any]:
            raise RuntimeError("revision mismatch: expected 'rev-fake', got 'other'")

    with pytest.raises(RuntimeError, match="revision mismatch"):
        _run_main(
            monkeypatch,
            tmp_path,
            pipeline_factory=lambda: _WrongRevisionPipeline(),
        )


def test_main_passport_warns_when_golden_coverage_low(
    monkeypatch: Any, tmp_path: Path
) -> None:
    # Один вопрос, эталонного источника в корпусе нет ⇒ recall не интерпретируется.
    dataset = _write_dataset(tmp_path, [_question("obs-01", ["docs/missing.md"])])

    out = _run_main(monkeypatch, tmp_path, dataset=dataset)

    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["golden_coverage"]["fully_covered"] == 0
    passport = (out / "PASSPORT.md").read_text(encoding="utf-8")
    assert "golden_coverage" in passport
    assert "не** характеризуют" in passport or "не характеризуют" in passport


def _vector_only_pipeline() -> _FakePipeline:
    return _FakePipeline([{"source_url": VECTOR_SOURCE, "axis": "vector", "relevance": 0.8}])


def test_main_passport_flags_inactive_graph_axis(monkeypatch: Any, tmp_path: Path) -> None:
    # Граф «готов», но ни одного источника по оси graph ⇒ вклад не измерен.
    out = _run_main(monkeypatch, tmp_path, pipeline_factory=_vector_only_pipeline)

    passport = (out / "PASSPORT.md").read_text(encoding="utf-8")
    assert "graph_axis_active" in passport
    report = json.loads((out / "lift_report.json").read_text(encoding="utf-8"))
    assert report["target"]["graph_axis_active"] is False
