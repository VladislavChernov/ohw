"""Eval-метрики (ADR-015): retrieval/генерация/lift — синтетика + фейк-judge."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from graphrag_proto.eval.metrics import (
    generation_metrics,
    lift_report,
    retrieval_metrics,
)


class _StubJudge:
    """Фейк-judge: детерминированные вердикты для groundedness/coverage."""

    def generate(self, prompt: str, system: str = "", stream: bool = True) -> Iterator[str]:
        if "Подтверждается ли" in prompt or "Содержится ли" in prompt:
            yield "yes"
        else:
            yield "- факт один\n- факт два"


def _dummy_judge() -> _StubJudge:
    return _StubJudge()


def test_recall_all_relevant_in_top() -> None:
    golden = {"s://a", "s://b"}
    m = retrieval_metrics(["s://a", "s://b", "s://c"], golden)
    assert m["recall_at_k"] == 1.0
    assert m["mrr_at_k"] == 1.0


def test_recall_partial() -> None:
    golden = {"s://a", "s://b", "s://c"}
    # В топ-5 попал 2 из 3 релевантных
    m = retrieval_metrics(["s://a", "s://x", "s://b", "s://y", "s://z"], golden)
    assert m["recall_at_k"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["precision_at_k"] == 2 / 5


def test_mrr_second_rank() -> None:
    golden = {"s://a"}
    m = retrieval_metrics(["s://x", "s://a"], golden)
    assert m["mrr_at_k"] == 0.5


def test_ndcg_ordering_matters() -> None:
    golden = {"s://a", "s://b"}
    ordered = retrieval_metrics(["s://a", "s://b", "s://z"], golden)
    reversed_order = retrieval_metrics(["s://z", "s://b", "s://a"], golden)
    assert ordered["ndcg_at_k"] > reversed_order["ndcg_at_k"]


def test_empty_retrieved() -> None:
    golden = {"s://a"}
    m = retrieval_metrics([], golden)
    assert m["recall_at_k"] == 0.0
    assert m["precision_at_k"] == 0.0
    assert m["mrr_at_k"] == 0.0
    assert m["ndcg_at_k"] == 0.0


def test_generation_metrics_judge_mode() -> None:
    """Стаб-judge всегда отвечает yes -> groundedness и coverage = 1.0."""
    m = generation_metrics("ответ", ["факт"], _StubJudge())
    assert m["mode"] == "judge"
    assert m["groundedness"] == 1.0
    assert m["coverage"] == 1.0


def test_generation_metrics_empty_answer() -> None:
    m = generation_metrics("", ["факт"], _StubJudge())
    assert m["mode"] == "empty"
    assert m["hallucination_rate"] == 1.0


def test_lift_report_pass() -> None:
    baseline = {"retrieval": {"recall_at_k": 0.5}, "generation": {"groundedness": 0.5, "coverage": 0.4}}
    target = {"retrieval": {"recall_at_k": 0.8}, "generation": {"groundedness": 0.7, "coverage": 0.6}}
    report = lift_report(baseline, target, revision="revX")
    assert report["verdict"] == "pass"
    assert report["delta"]["recall_at_k"] == 0.3
    assert report["revision"] == "revX"
    assert "run_id" in report and "created_at" in report


def test_lift_report_fail_on_regression() -> None:
    baseline = {"retrieval": {"recall_at_k": 0.5}, "generation": {"groundedness": 0.7, "coverage": 0.6}}
    target = {"retrieval": {"recall_at_k": 0.9}, "generation": {"groundedness": 0.5, "coverage": 0.6}}
    report = lift_report(baseline, target)
    assert report["verdict"] == "fail"


def test_lift_report_missing_generation_metrics() -> None:
    report = lift_report({"retrieval": {}}, {"retrieval": {"recall_at_k": 0.8}})
    assert report["verdict"] == "pass"  # нет groundedness/coverage -> 0>=0