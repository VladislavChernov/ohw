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
            result["effective_retrieval"] = {"graph_boost": 0.2, "max_depth": 2}
            result["trace"] = [
                *self._trace_events,
                {
                    "stage": "graph_expansion",
                    "seed_chunk_ids": ["c1"],
                    "paths": [["tag:it:a", "tag:it:b"]],
                    "depths": [1],
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
    # Применённый boost берётся из канала применённой конфигурации, а НЕ из trace-события.
    # В фикстуре trace-события `boost` больше нет намеренно: раньше поле читалось оттуда, и
    # при выключенном trace оно было всегда `None` — то есть запись о применённой настройке
    # зависела от отладочного флага.
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
        _run_eval.wait_jobs(["job"], poll_s=0, unreachable_timeout_s=1)


# --- B3: политика ожидания джоб — окна живости вместо общего дедлайна ---------


class _FakeClock:
    """Монотонные часы и сон без реальных секунд.

    внедрение источника времени (B3, п.4): проверки окон живости идут секунды
    модельного времени, поэтому гейт не ждёт стенд и не выжигает реальные минуты.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _job_body(
    status: str = "running",
    stage: str = "EXTRACT",
    ts: str = "2026-01-01T00:00:00+00:00",
    message: str = "",
) -> dict[str, Any]:
    """Тело GET /jobs/{id} в форме ingestion-сервиса (app.get_job)."""
    return {
        "status": status,
        "stage": stage,
        "error": None,
        "stages": [
            {"stage": stage, "status": "running", "message": message, "ts": ts},
        ],
    }


def _install_jobs(monkeypatch: Any, bodies: dict[str, Any]) -> None:
    """Подставить _get_json, раздающий заранее заданные тела по job_id."""

    def _fake_get(url: str) -> dict[str, Any]:
        job_id = url.rsplit("/", 1)[-1]
        body = bodies[job_id]
        return body() if callable(body) else body

    monkeypatch.setattr(_run_eval, "_get_json", _fake_get)


def test_wait_jobs_returns_when_every_job_completes(monkeypatch: Any) -> None:
    clock = _FakeClock()
    polls = {"j1": 0}

    def _j1() -> dict[str, Any]:
        polls["j1"] += 1
        if polls["j1"] < 2:
            return _job_body(ts=f"t{polls['j1']}")
        return _job_body(status="succeeded", stage="COMMIT", ts="done")

    _install_jobs(
        monkeypatch,
        {"j1": _j1, "j2": lambda: _job_body(status="succeeded", stage="COMMIT", ts="done")},
    )

    statuses = _run_eval.wait_jobs(
        ["j1", "j2"],
        poll_s=5.0,
        unreachable_timeout_s=300.0,
        job_timeout_s=900.0,
        ceiling_s=7200.0,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert set(statuses) == {"j1", "j2"}
    assert statuses["j2"]["status"] == "succeeded"
    # j1 завершился со второго опроса — значит, между опросами раннер действительно ждал
    assert clock.sleeps == [5.0]


def test_wait_jobs_unreachable_stand_names_no_specific_job(monkeypatch: Any) -> None:
    """Опросы не отвечают: это диагноз уровня стенда, а не «зависла джоба j1».

    Разделитель, а не порядок срабатывания: окно недоступности применяется только когда
    не удалось опросить НИ ОДНУ джобу. Поэтому конфигурация констант не решает, какое
    окно выстрелит, и одно из них не может стать мёртвым кодом.
    """
    clock = _FakeClock()

    def _boom(_url: str) -> dict[str, Any]:
        raise OSError("connection refused")

    monkeypatch.setattr(_run_eval, "_get_json", _boom)

    with pytest.raises(TimeoutError) as excinfo:
        _run_eval.wait_jobs(
            ["j1", "j2"],
            poll_s=5.0,
            unreachable_timeout_s=300.0,
            job_timeout_s=900.0,
            ceiling_s=7200.0,
            clock=clock,
            sleeper=clock.sleep,
        )

    message = str(excinfo.value)
    assert "не отвечает" in message
    assert "300" in message


def test_wait_jobs_unreachable_window_survives_smaller_job_timeout(
    monkeypatch: Any,
) -> None:
    """`job_timeout` меньше окна недоступности не отменяет диагностику стенда.

    Раньше окна конкурировали по порядку, и при таком соотношении одно становилось
    мёртвым кодом. Теперь условия взаимоисключающие: опросы падают ⇒ применимо окно
    недоступности независимо от величины `job_timeout`.
    """
    clock = _FakeClock()

    def _boom(_url: str) -> dict[str, Any]:
        raise OSError("connection refused")

    monkeypatch.setattr(_run_eval, "_get_json", _boom)

    with pytest.raises(TimeoutError, match="не отвечает"):
        _run_eval.wait_jobs(
            ["j1"],
            poll_s=5.0,
            unreachable_timeout_s=300.0,
            job_timeout_s=30.0,
            ceiling_s=7200.0,
            clock=clock,
            sleeper=clock.sleep,
        )


def test_wait_jobs_single_pending_job_reports_its_stage(monkeypatch: Any) -> None:
    """Хвост батча: опросы идут, не завершена одна джоба — виновата именно она.

    Именно этот случай раньше диагностировался как «стенд мёртв»: при одном висящем
    прогресса не было ни у кого. Теперь отсутствие прогресса при успешных опросах —
    это per-job, а стенд недоступен только когда не отвечает всё.
    """
    clock = _FakeClock()
    _install_jobs(monkeypatch, {"j1": _job_body(), "j2": _job_body(stage="COMMIT")})

    with pytest.raises(TimeoutError) as excinfo:
        _run_eval.wait_jobs(
            ["j1", "j2"],
            poll_s=5.0,
            unreachable_timeout_s=300.0,
            job_timeout_s=900.0,
            ceiling_s=7200.0,
            clock=clock,
            sleeper=clock.sleep,
        )

    message = str(excinfo.value)
    assert "не продвинулись" in message
    assert "900" in message
    # диагностика стадии каждой висящей джобы сохраняется (не голый список id)
    assert "j1@EXTRACT" in message
    assert "j2@COMMIT" in message


def test_wait_jobs_message_only_change_is_not_progress(monkeypatch: Any) -> None:
    """note_stage меняет только message — это не прогресс.

    Иначе degradation-нотиса (B1) держал бы прогон живым, хотя стадия не
    двигается: молчаливое ожидание вместо диагностируемого таймаута.
    """
    clock = _FakeClock()
    _install_jobs(
        monkeypatch,
        {"j1": lambda: _job_body(message=f"enrichment_degraded: {clock.now:.0f}")},
    )

    with pytest.raises(TimeoutError, match="не продвинулись"):
        _run_eval.wait_jobs(
            ["j1"],
            poll_s=5.0,
            unreachable_timeout_s=300.0,
            job_timeout_s=900.0,
            ceiling_s=7200.0,
            clock=clock,
            sleeper=clock.sleep,
        )


def test_wait_jobs_our_own_bug_is_not_swallowed_as_unreachable(monkeypatch: Any) -> None:
    """Дефект кода опроса обязан упасть, а не выглядеть как «стенд молчит».

    Ловится только `RuntimeError` (приведённая ошибка HTTP-контура) и `OSError`
    (недоступность). Всё остальное — наш баг: если его съесть тем жеприёмом, что и
    недоступность стенда, диагностика снова станет источником неверных выводов.
    """
    clock = _FakeClock()

    def _our_bug(_url: str) -> dict[str, Any]:
        raise AttributeError("NoneType has no attribute 'get'")

    monkeypatch.setattr(_run_eval, "_get_json", _our_bug)

    with pytest.raises(AttributeError):
        _run_eval.wait_jobs(
            ["j1"],
            poll_s=5.0,
            unreachable_timeout_s=300.0,
            job_timeout_s=900.0,
            ceiling_s=7200.0,
            clock=clock,
            sleeper=clock.sleep,
        )


def test_wait_jobs_per_job_window_names_only_stuck_job(monkeypatch: Any) -> None:
    """Одна зависшая джоба при живых соседях: виновата именно она."""
    clock = _FakeClock()

    def _live(name: str) -> Any:
        def _body() -> dict[str, Any]:
            return _job_body(ts=f"{name}-{clock.now:.0f}")

        return _body

    _install_jobs(
        monkeypatch,
        {"live1": _live("a"), "live2": _live("b"), "stuck": _job_body(stage="VALIDATE")},
    )

    with pytest.raises(TimeoutError) as excinfo:
        _run_eval.wait_jobs(
            ["live1", "live2", "stuck"],
            poll_s=5.0,
            unreachable_timeout_s=300.0,
            job_timeout_s=900.0,
            ceiling_s=7200.0,
            clock=clock,
            sleeper=clock.sleep,
        )

    message = str(excinfo.value)
    assert "не продвинулись" in message
    assert "stuck@VALIDATE" in message
    assert "live1" not in message
    assert "live2" not in message


def test_wait_jobs_hard_ceiling_stops_slow_but_alive(monkeypatch: Any) -> None:
    """Backstop: джоба ползёт, прогресс есть — останавливает только общий кап."""
    clock = _FakeClock()
    _install_jobs(monkeypatch, {"j1": lambda: _job_body(ts=f"t{clock.now:.0f}")})

    with pytest.raises(TimeoutError) as excinfo:
        _run_eval.wait_jobs(
            ["j1"],
            poll_s=5.0,
            unreachable_timeout_s=300.0,
            job_timeout_s=900.0,
            ceiling_s=1000.0,
            clock=clock,
            sleeper=clock.sleep,
        )

    message = str(excinfo.value)
    assert "жёсткий кап" in message
    assert "1000" in message
    assert "j1@EXTRACT" in message


def test_config_state_names_identity_and_applied_settings() -> None:
    """Артефакт обязан называть, чем измерен: иначе он неполон по построению.

    Версия кода конфигурацию не определяет, а изменить профиль посреди прогона можно было
    всегда. Три величины — идентичность, момент применения, право на устаревание — плюс
    фактически применённые настройки.
    """
    records = [
        {
            "mode": "baseline",
            "profile_fingerprint": "abc123def456",
            "profile_pinned_at": "2026-09-27T10:00:00+00:00",
            "config_fallbacks": [],
            "effective_retrieval": {"max_depth": 2, "graph_boost": 0.0},
        },
        {
            "mode": "target",
            "profile_fingerprint": "abc123def456",
            "profile_pinned_at": "2026-09-27T10:00:00+00:00",
            "config_fallbacks": ["retrieval.graph_boost"],
            "effective_retrieval": {"max_depth": 2, "graph_boost": 0.0},
        },
    ]

    state = _run_eval.build_config_state({"baseline": {}, "target": {}}, records)

    assert state["profile_fingerprint"] == "abc123def456"
    assert state["profile_staleness"] == "immutable_for_session"
    assert state["config_drift"] is False
    assert state["config_fallbacks"] == {"retrieval.graph_boost": 1}
    assert state["effective_retrieval"]["max_depth"] == 2


def test_config_state_detects_drift_between_modes() -> None:
    """Два разных отпечатка в одной паре — конфигурация плыла, и это ломает delta.

    Именно тот случай, ради которого закрепление недостаточно само по себе: два режима
    закрепляют профиль каждый в свой момент, и между ними файл могли изменить.
    """
    records = [
        {"mode": "baseline", "profile_fingerprint": "aaa", "profile_pinned_at": "t0"},
        {"mode": "target", "profile_fingerprint": "bbb", "profile_pinned_at": "t1"},
    ]

    state = _run_eval.build_config_state({"baseline": {}, "target": {}}, records)

    assert state["config_drift"] is True
    assert state["profile_fingerprints"] == {"baseline": "aaa", "target": "bbb"}


def test_config_state_survives_pipeline_that_never_ran() -> None:
    """Пайплайн, не построившийся, не должен ломать сбор состояния конфигурации.

    Иначе первая же ошибка сборки превращается в «нет отпечатка» — то есть в отсутствие
    данных там, где на самом деле их просто ещё не могло быть.
    """
    state = _run_eval.build_config_state({"baseline": {}}, [])

    assert state["profile_fingerprint"] is None
    assert state["config_drift"] is False
    assert state["profile_staleness"] == "immutable_for_session"


def test_run_manifest_records_resolved_timeouts(monkeypatch: Any) -> None:
    """Пороги ожидания — часть условий прогона: без них отчёт не интерпретируем."""
    monkeypatch.setenv("RUN_CODE_COMMIT", "host-hash")
    timeouts = {
        "unreachable_timeout_s": 300.0,
        "job_timeout_s": 900.0,
        "wait_ceiling_s": 7200.0,
        "slot_timeout_s": 300.0,
        "submit_budget_s": 7200.0,
    }

    manifest = _run_eval.build_run_manifest(
        run_id="eval_t",
        started_at="2026-01-01T00:00:00Z",
        domain="it",
        revision="rev-fake",
        datasets=["q.jsonl"],
        mode_arg="both",
        k=5,
        judge=None,
        flags=[],
        timeouts=timeouts,
    )

    assert manifest["timeouts"] == timeouts


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

def test_build_ingest_report_surfaces_enrichment_degradation() -> None:
    """Деградация optional-ингеста обязана быть видна в отчёте.

    Иначе прогон с частичным графом неотличим от прогона с полным: в обоих
    случаях статус `succeeded`, а рёбер просто меньше. Метрики начинают
    сравнивать прогоны с разной полнотой графа.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.5},
    ]
    statuses = {
        "j1": {
            "status": "succeeded",
            "stage": "COMMIT",
            "error": None,
            "signals": {"enrichment_degraded": "EXTRACT"},
            "stages": [
                {"stage": "EXTRACT", "status": "succeeded", "message": "enrichment_degraded: glossary 503"},
            ],
        }
    }

    report = _run_eval.build_ingest_report(submitted, statuses)

    doc = report["documents"][0]
    assert doc["enrichment_degraded"] is True
    assert doc["enrichment_error"] == "glossary 503"
    assert doc["llm_layer_dropped"] is False
    assert report["enrichment_degraded_documents"] == 1
    assert report["llm_layer_dropped_documents"] == 0

def test_build_ingest_report_separates_layer_loss_from_degradation() -> None:
    """Деградация и потеря слоя — разные факты, и счётчики их разделяют.

    Флаг деградации ставится в двух местах с противоположным смыслом: профиль
    домена не загрузился (ничего не потеряно) и исключение в экстракции (потерян
    весь слой). Одна сумма неинтерпретируема: высокое значение может означать
    нулевую потерю, и наоборот.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
        {"job_id": "j2", "source_url": "src://b.txt", "relpath": "b.txt", "bytes": 100, "waited_s": 2.0},
    ]
    statuses = {
        # профиль не загрузился: деградация есть, слой не терялся
        "j1": {
            "status": "succeeded",
            "stage": "COMMIT",
            "error": None,
            "signals": {"enrichment_degraded": "EXTRACT"},
            "stages": [
                {"stage": "EXTRACT", "status": "succeeded", "message": "enrichment_degraded: profile 503"},
            ],
        },
        # исключение в экстракции: слой потерян целиком
        "j2": {
            "status": "succeeded",
            "stage": "COMMIT",
            "error": None,
            "signals": {"enrichment_degraded": "EXTRACT", "llm_layer_dropped": "EXTRACT"},
            "stages": [
                {
                    "stage": "EXTRACT",
                    "status": "succeeded",
                    "message": "enrichment_degraded: relationship ссылается на неизвестную сущность",
                },
            ],
        },
    }

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["enrichment_degraded_documents"] == 2
    assert report["llm_layer_dropped_documents"] == 1
    dropped = next(d for d in report["documents"] if d["job_id"] == "j2")
    assert dropped["enrichment_error"] == "relationship ссылается на неизвестную сущность"
    kept = next(d for d in report["documents"] if d["job_id"] == "j1")
    assert kept["llm_layer_dropped"] is False


def _job_with_extraction(
    *,
    llm_records: int,
    cause: str | None = None,
    lost_entities: int = 0,
    degraded: bool = False,
    layer_dropped: bool = False,
) -> dict[str, Any]:
    signals: dict[str, str] = {"extraction:llm:it@1:abc": "EXTRACT"}
    if degraded:
        signals["enrichment_degraded"] = "EXTRACT"
    if layer_dropped:
        signals["llm_layer_dropped"] = "EXTRACT"
    return {
        "status": "succeeded",
        "stage": "COMMIT",
        "error": None,
        "signals": signals,
        "enrichment": {
            "cause": cause,
            "lost_entities": lost_entities,
            "lost_edges": 0,
            "llm_records": llm_records,
            "llm_edges": 0,
        },
        "stages": [{"stage": "EXTRACT", "status": "succeeded", "message": ""}],
    }


def _noop_job() -> dict[str, Any]:
    """Джоба no-op: INGEST признал содержимое неизменным, пайплайн не пошёл дальше.

    `stages` содержит ВСЕ девять стадий — ровно как на стенде. Это и есть дефект прибора,
    который закрыт ADR-049: `Executor` пишет стадию перед запуском, поэтому журнал не
    отличает отработавшую стадию от пропущенной, и джоба, где модель не звали ни разу,
    выглядит как прошедшая все девять. Признак no-op приходит сигналом `ingest_noop`.
    """
    return {
        "status": "succeeded",
        "stage": "COMMIT",
        "error": None,
        "signals": {"ingest_noop": "INGEST"},
        "stages": [
            {"stage": name, "status": "succeeded", "message": ""}
            for name in (
                "CHUNK",
                "COMMIT",
                "CONTRACT",
                "DEDUP",
                "EMBED",
                "EXTRACT",
                "INGEST",
                "NORMALIZE",
                "VALIDATE",
            )
        ],
    }


def _noop_job_legacy_shape() -> dict[str, Any]:
    """Форма джобы no-op ДО ADR-049: последняя стадия INGEST, сигналов нет.

    Оставлена отдельной функцией, а не удалена: показывать, что именно было исправлено.
    Прибор выводил no-op из `stage == "INGEST"` и по этой форме был прав — а на стенде
    получал не эту форму, потому что `Executor` пишет стадию перед запуском и доходит до
    COMMIT. То есть исправление касается не формы ответа, а основания вывода.
    """
    return {
        "status": "succeeded",
        "stage": "INGEST",
        "error": None,
        "signals": {},
        "stages": [{"stage": "INGEST", "status": "succeeded", "message": "noop"}],
    }


def _job_with_nodes(by_version: dict[str, int], status: str = "committed") -> dict[str, Any]:
    """Джоба, у которой запись в граф состоялась, с нодами по производителям."""
    job = _job_with_extraction(llm_records=sum(by_version.values()))
    job["nodes_by_extractor"] = {
        "by_extractor_version": dict(by_version),
        "graph_projection_status": status,
    }
    return job


def test_ingest_report_names_producer_of_every_written_node() -> None:
    """ADR-049 п. 4: отчёт называет, кто создал записанные ноды.

    До этого изменения вопрос «это сущности модели или слова текста» решался только
    осмотром базы: в прогоне 2026-10-04 в отчёте стояло `llm_records_extracted: 0`,
    `enrichment_degraded: false`, а в графе лежало 1391 нода с меткой заглушки. Читать
    отчёт было нечем.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
        {"job_id": "j2", "source_url": "src://b.txt", "relpath": "b.txt", "bytes": 100, "waited_s": 1.0},
    ]
    statuses = {
        "j1": _job_with_nodes({"llm:it@1:97efaf964b1b": 39, "user:manual": 1}),
        "j2": _job_with_nodes({"llm:it@1:97efaf964b1b": 12}),
    }

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["nodes_by_extractor_version"] == {
        "llm:it@1:97efaf964b1b": 51,
        "user:manual": 1,
    }
    assert report["llm_nodes"] == 51
    assert report["nodes_written"] == 52
    assert report["documents_with_nodes_recorded"] == 2


def test_ingest_report_tells_no_nodes_apart_from_nothing_measured() -> None:
    """Пустое распределение и отсутствие записи — разные результаты, и оба обязаны быть видны.

    «Нод ноль» и «мы не смотрели» сливаются в один и тот же `{}`, если не сказать, была ли
    запись вообще. Это тот же молчащий ноль, из-за которого дефект прожил два отчёта:
    джоба без записи в граф выглядит как джоба, где сущностей не нашлось.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
        {"job_id": "j2", "source_url": "src://b.txt", "relpath": "b.txt", "bytes": 100, "waited_s": 1.0},
    ]
    statuses = {
        "j1": _job_with_nodes({}, status="committed"),
        "j2": _noop_job(),
    }

    report = _run_eval.build_ingest_report(submitted, statuses)

    measured = next(d for d in report["documents"] if d["job_id"] == "j1")
    unmeasured = next(d for d in report["documents"] if d["job_id"] == "j2")
    assert measured["nodes_recorded"] is True
    assert measured["graph_projection_status"] == "committed"
    assert measured["nodes_by_extractor_version"] == {}
    assert unmeasured["nodes_recorded"] is False
    assert unmeasured["graph_projection_status"] is None
    assert report["documents_with_nodes_recorded"] == 1


def test_ingest_report_counts_llm_nodes_only_by_its_own_prefix() -> None:
    """`llm_nodes` - ноды модели, а не все ноды.

    Ручной тег - законная нода (пользователь задал её осознанно), она попадает и в
    `nodes_written`, и в `nodes_by_extractor_version`, но в `llm_nodes` не попадает: модель
    её не производила.

    Проверяются оба уровня, документ и отчёт. Одноимённый ключ в двух местах одного JSON
    расходился: документ суммировал всех производителей, отчёт только префикс `llm:`.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
    ]
    statuses = {"j1": _job_with_nodes({"llm:it@1:97efaf964b1b": 4, "user:manual": 3})}

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["llm_nodes"] == 4
    assert report["nodes_written"] == 7
    assert report["documents"][0]["llm_nodes"] == 4
    assert report["documents"][0]["nodes_by_extractor_version"] == {
        "llm:it@1:97efaf964b1b": 4,
        "user:manual": 3,
    }


def test_job_that_never_reached_commit_is_not_counted_as_recorded() -> None:
    """Джоба без записи состоявшихся нод - это `не измерено`, а не «нод ноль» (ADR-049 п. 4).

    Сервис отдаёт поле `nodes_by_extractor` всегда, а строка в реестре появляется только
    когда дошла до COMMIT. Упавшая джоба и no-op-джоба не доходят - значит строки нет.
    Разбор обязан отличать «строки нет» от «запись состоялась и нод ноль», иначе
    `documents_with_nodes_recorded` считает документы, про которые никто ничего не знает,
    и молчание снова выглядит как результат.

    Формулировка контракта, а не реализации: тест накрывает все три наблюдаемые формы
    (ключ отсутствует, `{}`, `None`) - форма ответа может меняться, запрет не должен.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://failed.txt", "relpath": "failed.txt", "bytes": 100, "waited_s": 1.0},
        {"job_id": "j2", "source_url": "src://noop.txt", "relpath": "noop.txt", "bytes": 100, "waited_s": 1.0},
        {"job_id": "j3", "source_url": "src://written.txt", "relpath": "written.txt", "bytes": 100, "waited_s": 1.0},
    ]
    failed = _job_with_extraction(llm_records=9)
    failed["status"] = "failed"
    failed["stage"] = "EXTRACT"
    failed["nodes_by_extractor"] = {}
    noop = _noop_job()
    noop["nodes_by_extractor"] = None
    statuses = {
        "j1": failed,
        "j2": noop,
        "j3": _job_with_nodes({"llm:it@1:97efaf964b1b": 0}),
    }

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert [doc["nodes_recorded"] for doc in report["documents"]] == [False, False, True]
    assert report["documents_with_nodes_recorded"] == 1
    # Запись состоялась, но нод ноль - единственный честный ноль в отчёте.
    assert report["documents"][2]["nodes_recorded"] is True
    assert report["documents"][2]["llm_nodes"] == 0
    assert report["llm_nodes"] == 0
    assert report["nodes_written"] == 0


def test_ingest_report_counts_loss_size_not_only_documents() -> None:
    """«Сколько документов» и «сколько фактов исчезло» — разные числа, и оба нужны.

    Частота отвечает на вопрос «как часто ломается», размер — «сколько это стоит».
    Из одного флага получается только частота, а потеря на одном документе может быть
    и двух записей, и двух тысяч; решение о починке принимается по второму.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
    ]
    statuses = {
        "j1": _job_with_extraction(
            llm_records=77,
            cause="model_error",
            lost_entities=77,
            degraded=True,
            layer_dropped=True,
        )
    }
    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["llm_layer_dropped_documents"] == 1
    assert report["llm_layer_lost_entities"] == 77
    assert report["enrichment_causes"] == {"model_error": 1}


def test_ingest_report_denominator_is_documents_where_the_model_answered() -> None:
    """Знаменатель — документы, где модель ОТВЕЧАЛА, а не те, где стадия стоит в журнале.

    Два разных вопроса, и раньше они были смешаны в один (`extraction_stage_ran`). У
    no-op-джобы в журнале лежат все девять старий, поэтому как знаменатель объёма этот
    признак вводил в заблуждение: на прогоне 2026-10-04 три no-op-джобы попали в него с
    нулём записей, и «записей на документ» считалось по документам, где извлечения не
    было (ADR-049).
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
        {"job_id": "j2", "source_url": "src://b.txt", "relpath": "b.txt", "bytes": 100, "waited_s": 0.0},
    ]
    statuses = {"j1": _job_with_extraction(llm_records=40), "j2": _noop_job()}

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["documents_with_llm_extraction"] == 1
    assert report["llm_records_extracted"] == 40
    assert report["llm_records_per_extraction_document"] == 40.0
    # Журнал у no-op-джобы полон — это факт журнала, и он остаётся в отчёте отдельным полем,
    # чтобы расхождение двух признаков было видно, а не спрятано.
    noop_doc = next(d for d in report["documents"] if d["job_id"] == "j2")
    assert noop_doc["llm_extraction_ran"] is False
    assert noop_doc["extraction_stage_reached"] is True
    assert noop_doc["noop"] is True


def test_noop_is_read_from_a_signal_not_from_the_last_stage() -> None:
    """Регрессия на класс дефекта, а не на его единичный случай (ADR-049).

    Сценарий ровно стендовский: `Executor` пишет стадию ПЕРЕД запуском, поэтому у джобы,
    остановленной на INGEST как no-op, последняя стадия — `COMMIT`, а в журнале лежат все
    девять строк. Прибор, выводящий no-op из последней стадии, объявляет такую джобу
    холодной перезагрузкой — и прогон 2026-10-04 был отчитан именно так: `noop: false`,
    `last_stage: COMMIT`, `extraction_stage_ran: true`, `llm_records_extracted: 0`.

    Контракт положительный: признак no-op приходит из сигнала, который ставит сервис.
    Отрицание «последняя стадия не бывает COMMIT у no-op» в тест не входит — оно стало бы
    правдой только по счастливой форме ответа.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 0.0},
    ]

    report = _run_eval.build_ingest_report(submitted, {"j1": _noop_job()})

    doc = report["documents"][0]
    assert doc["noop"] is True, "no-op обязан читаться из сигнала, а не из последней стадии"
    assert doc["last_stage"] == "COMMIT", "сценарий повторяет стенд: стадия доходит до COMMIT"
    assert doc["llm_extraction_ran"] is False
    assert report["noop_documents"] == 1
    assert report["cold_documents"] == 0
    # Знаменатель объёма не должен считать джобу, где модель не отвечала.
    assert report["documents_with_llm_extraction"] == 0
    assert report["llm_records_per_extraction_document"] is None


def test_noop_without_the_signal_is_not_guessed_from_the_stage_log() -> None:
    """Признак no-op читается ТОЛЬКО из сигнала; догадки по журналу не осталось (ADR-049).

    Здесь фиксируется решение, а не совместимость. Прежний прибор выводил no-op из
    `stage == "INGEST"`, и на стенде это давало ложь: `Executor` пишет стадию перед
    запуском, поэтому джоба, остановленная на INGEST, доходит в журнале до `COMMIT` и
    отчитывалась как холодная перезагрузка с нулевым извлечением.

    Совместимость со старой формой ответа могла бы выглядеть аккуратно — «узнаём и по
    старой, и по новой форме». Но старая форма и есть источник дефекта: возвращая её как
    fallback, мы восстанавливаем ровно то поведение, которое чинили, и оно снова начнёт
    срабатывать на любой джобе, чей последней статией случайно окажется INGEST.
    Сервис и прибор выкатываются из одного образа, рассинхронизации здесь нет.

    Контракт положительный: джоба, у которой нет сигнала `ingest_noop`, не объявляется
    no-op. Отрицание «стадия INGEST не бывает последней» в тест не входит — оно было бы
    правдой лишь по счастливой форме.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 0.0},
    ]
    legacy = _noop_job_legacy_shape()

    assert _run_eval._ingest_noop(legacy) is False
    report = _run_eval.build_ingest_report(submitted, {"j1": legacy})
    assert report["documents"][0]["noop"] is False


def test_ingest_report_does_not_publish_zero_percent_on_empty_base() -> None:
    """Нулевой знаменатель — это «нечего мерить», а не «модель вернула ноль».

    Печатать `0%` на пустой базе нельзя: это самое благоприятное прочтение из
    возможных, и именно его проще всего пропустить. Поле обязано быть None.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 0.0},
    ]

    report = _run_eval.build_ingest_report(submitted, {"j1": _noop_job()})

    assert report["documents_with_llm_extraction"] == 0
    assert report["llm_records_per_extraction_document"] is None
    assert report["llm_records_extracted"] == 0


def test_ingest_report_names_documents_where_counter_could_be_wrong() -> None:
    """Стадия была, а записей ноль — названное число, а не тихий ноль.

    Само по себе это не поломка: модель действительно могла ничего не найти. Но такой
    документ обязан быть виден, иначе сломанный счётчик и честный ноль выглядят
    одинаково — а это ровно тот случай, где молчание дороже всего.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
        {"job_id": "j2", "source_url": "src://b.txt", "relpath": "b.txt", "bytes": 100, "waited_s": 1.0},
    ]
    statuses = {"j1": _job_with_extraction(llm_records=0), "j2": _job_with_extraction(llm_records=7)}

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["documents_with_llm_extraction"] == 2
    assert report["documents_with_llm_extraction_without_records"] == 1
    assert report["llm_records_per_extraction_document"] == 3.5


def test_ingest_quality_flags_loss_only_when_facts_disappeared() -> None:
    """`loss` — про исчезнувшие факты, а не про сработавший путь сброса.

    Разница ровно та, что делает счётчик пригодным: деградация без потери (профиль не
    загрузился) и потеря после первого ответа модели — разные события, и сваливать их
    в один «слой потерян» значит завышать потерю.
    """
    degraded_only = _run_eval.build_ingest_quality(
        _run_eval.build_ingest_report(
            [{"job_id": "j1", "source_url": "s", "relpath": "a", "bytes": 1, "waited_s": 1.0}],
            {
                "j1": _job_with_extraction(
                    llm_records=0, cause="profile_unavailable", degraded=True
                )
            },
        )
    )
    assert degraded_only["loss"] is False
    assert degraded_only["enrichment_degraded_documents"] == 1

    lost = _run_eval.build_ingest_quality(
        _run_eval.build_ingest_report(
            [{"job_id": "j1", "source_url": "s", "relpath": "a", "bytes": 1, "waited_s": 1.0}],
            {
                "j1": _job_with_extraction(
                    llm_records=9,
                    cause="model_error",
                    lost_entities=9,
                    degraded=True,
                    layer_dropped=True,
                )
            },
        )
    )
    assert lost["loss"] is True
    assert lost["llm_layer_lost_entities"] == 9
    assert lost["enrichment_causes"] == {"model_error": 1}


def test_ingest_quality_of_absent_report_is_clean_not_missing() -> None:
    """Прогон без ингеста (векторная ось) даёт нули, а не «неизвестно».

    `None` здесь означал бы, что потери нельзя исключить, и вердикт потерял бы
    право быть `pass` у прогонов, где ингеста просто не было.
    """
    quality = _run_eval.build_ingest_quality(None)

    assert quality["loss"] is False
    assert quality["llm_layer_dropped_documents"] == 0


def test_ingest_report_survives_unreadable_numeric_fields() -> None:
    """Мусор в числовом поле обнуляет значение, а не роняет отчёт.

    Отчёт о потере, который падает на одном нечитаемом значении, хуже отсутствия
    отчёта: вместо данных о потере получается стоп-слово, и потерю перестают замечать.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
    ]
    job = _job_with_extraction(llm_records=5)
    job["enrichment"]["lost_entities"] = "много"  # type: ignore[assignment]
    statuses = {"j1": job}

    report = _run_eval.build_ingest_report(submitted, statuses)

    assert report["llm_layer_lost_entities"] == 0
    assert report["llm_records_extracted"] == 5


def test_both_flags_come_from_one_structural_channel() -> None:
    """Оба факта приходят полем `signals`, а не текстом и не разными каналами.

    Если деградация читается из сообщения по префиксу, а потеря — из поля, у потребителя
    два механизма, и объяснение «почему так» становится историческим вместо замысла.
    Сообщение стадии при этом переформатировано полностью: счётчики обязаны от этого
    не измениться, потому что они читаются не из текста.
    """
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.0},
    ]

    def _job(signals: dict[str, str], message: str) -> dict[str, Any]:
        return {
            "status": "succeeded",
            "stage": "COMMIT",
            "error": None,
            "signals": signals,
            "stages": [{"stage": "EXTRACT", "status": "succeeded", "message": message}],
        }

    both = {"enrichment_degraded": "EXTRACT", "llm_layer_dropped": "EXTRACT"}
    as_is = _run_eval.build_ingest_report(
        submitted, {"j1": _job(both, "enrichment_degraded: relation unknown")}
    )
    reformatted = _run_eval.build_ingest_report(
        submitted, {"j1": _job(both, "DEGRADED! relation unknown -- v2 format")}
    )
    for report in (as_is, reformatted):
        assert report["enrichment_degraded_documents"] == 1
        assert report["llm_layer_dropped_documents"] == 1
    # причина — свободный текст, и при смене формата она закономерно теряется
    assert as_is["documents"][0]["enrichment_error"] == "relation unknown"
    assert reformatted["documents"][0]["enrichment_error"] is None


def test_build_ingest_report_no_degradation_is_explicitly_false() -> None:
    """Чистый прогон обязан отличаться от деградировавшего, а не молчать."""
    submitted = [
        {"job_id": "j1", "source_url": "src://a.txt", "relpath": "a.txt", "bytes": 100, "waited_s": 1.5},
    ]
    statuses = {"j1": {"status": "succeeded", "stage": "COMMIT", "error": None, "stages": []}}

    report = _run_eval.build_ingest_report(submitted, statuses)

    doc = report["documents"][0]
    assert doc["enrichment_degraded"] is False
    assert doc["enrichment_error"] is None
    assert doc["llm_layer_dropped"] is False
    assert report["enrichment_degraded_documents"] == 0
    assert report["llm_layer_dropped_documents"] == 0


# --- ADR-045: «не измерено» всегда с причиной ---------------------------------


def _contribution_record(mode: str, projection_status: str | None) -> dict[str, Any]:
    return {
        "graph_contribution": {"mode": mode},
        "projection_status": projection_status,
    }


def test_unmeasured_aggregate_carries_the_cause_into_the_artifact() -> None:
    """Позитивный контракт ADR-045: вклад не измерен — значит в артефакте есть причина.

    Формулировка проверяет не «этих трёх поля не должно быть», а «причина обязана быть».
    Причина живёт в `projection_status` по каждому вопросу, и раньше доходила только до
    `qa_log.jsonl`, из-за чего случай без вырожденных вопросов оставался с напечатанным
    `necessity = 0.0` и без единого предупреждения.
    """
    aggregate = _run_eval._aggregate_graph_contribution(
        [
            _contribution_record("degraded", "pending"),
            _contribution_record("degraded", "pending"),
        ]
    )

    assert aggregate["mode"] == "not_measured"
    assert aggregate["projection_statuses"] == ["pending"]


def test_measured_aggregate_reports_nothing_about_projection_statuses() -> None:
    """Обратная сторона: при `measured` поле причин не появляется.

    Условие теста, а не замороженный список: добавление нового режима не должно ломать
    проверку, а потеря причины при `not_measured` — должна.
    """
    aggregate = _run_eval._aggregate_graph_contribution(
        [
            {
                "graph_contribution": {
                    "mode": "measured",
                    "necessity": 0.5,
                    "delta_recall": 0.25,
                    "recall_graph": 0.5,
                    "recall_vector": 0.25,
                    "evidence_recall_graph": 0.5,
                },
                "projection_status": "ready",
            }
        ]
    )

    assert aggregate["mode"] == "measured"
    assert "projection_statuses" not in aggregate


def test_unmeasured_aggregate_warns_even_without_degraded_questions(tmp_path: Path) -> None:
    """Оговорка ставится по `mode`, а не по счётчику.

    Именно этот случай не был покрыт: `degraded_questions = 0` (среза в наборе нет),
    `mode = not_measured`, число `0.0` напечатано — и рядом с ним ни одного слова о том,
    что это «не считали». `docs/test_plan.md` §4 называет такой вывод ложноотрицательным.
    """
    aggregate = _run_eval._aggregate_graph_contribution(
        [_contribution_record("not_measured", None)]
    )
    assert aggregate["degraded_questions"] == 0
    report = {
        "run_id": "r1",
        "verdict": "n/a",
        "target": {"graph_contribution": aggregate},
        "retrieval": {},
        "golden": {},
        "config": {},
    }

    _run_eval.write_lift_report(report, tmp_path)
    text = (tmp_path / "lift_report.md").read_text(encoding="utf-8")

    assert "**necessity**: 0.0" in text
    assert "это «не считали», а не результат" in text


def test_measured_graph_contribution_carries_no_caveat(tmp_path: Path) -> None:
    """Обратная сторона: при `measured` оговорки нет, иначе она станет шумом."""
    report = {
        "run_id": "r1",
        "verdict": "n/a",
        "target": {
            "graph_contribution": {
                "mode": "measured",
                "questions": 8,
                "necessity": 0.25,
                "delta_recall": 0.1,
            }
        },
        "retrieval": {},
        "golden": {},
        "config": {},
    }

    _run_eval.write_lift_report(report, tmp_path)
    text = (tmp_path / "lift_report.md").read_text(encoding="utf-8")

    assert "**necessity**: 0.25" in text
    assert "не считали" not in text
