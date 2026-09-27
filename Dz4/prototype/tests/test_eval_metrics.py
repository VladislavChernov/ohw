"""Eval-метрики (ADR-015): retrieval/генерация/lift — синтетика + фейк-judge."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from graphrag_proto.eval.metrics import (
    generation_metrics,
    graph_contribution,
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


def test_retrieval_metrics_dedup_cross_axis_duplicate() -> None:
    """BUG-фикс: дубль одного URL (графовая+векторная оси) не завышает метрики.

    Было: recall_at_k=2.0 и ndcg_at_k=1.6309 при одном релевантном; после dedup
    по первому вхождению (лучший ранг) метрика считается по уникальным URL.
    """
    golden = {"s://shared"}
    m = retrieval_metrics(["s://shared", "s://shared"], golden)
    assert m["recall_at_k"] == 1.0
    assert m["precision_at_k"] == 0.2  # 1 уникальный «попадающий» из k=5
    assert m["ndcg_at_k"] == 1.0
    assert m["mrr_at_k"] == 1.0

    two_golden = {"s://a", "s://b"}
    dup = retrieval_metrics(["s://a", "s://a", "s://b"], two_golden)
    assert dup["recall_at_k"] == 1.0  # было 1.5
    assert dup["precision_at_k"] == 0.4  # 2 уникальных из 5, а не 3 из 5


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


def test_lift_report_marks_degraded_target_invalid() -> None:
    baseline = {
        "retrieval": {"recall_at_k": 0.5},
        "generation": {"groundedness": 0.7, "coverage": 0.6},
    }
    target = {
        "retrieval": {"recall_at_k": 0.8},
        "generation": {"groundedness": 0.8, "coverage": 0.7},
        "graph_contribution": {"degraded_questions": 1},
    }

    report = lift_report(baseline, target)

    assert report["verdict"] == "invalid"


def test_lift_report_fail_on_regression() -> None:
    baseline = {"retrieval": {"recall_at_k": 0.5}, "generation": {"groundedness": 0.7, "coverage": 0.6}}
    target = {"retrieval": {"recall_at_k": 0.9}, "generation": {"groundedness": 0.5, "coverage": 0.6}}
    report = lift_report(baseline, target)
    assert report["verdict"] == "fail"


def _ingest_quality(**kwargs: object) -> dict[str, Any]:
    base = {
        "llm_layer_dropped_documents": 0,
        "llm_layer_lost_entities": 0,
        "llm_layer_lost_edges": 0,
        "enrichment_degraded_documents": 0,
        "loss": False,
    }
    base.update(kwargs)
    return base


def test_lift_report_invalidates_when_target_lost_llm_layer() -> None:
    """Потеря слоя инвалидирует прогон, даже когда метрики выглядят улучшением.

    Смещение одностороннее: пропавшие факты нельзя ни найти, ни вспомнить, поэтому
    недооценка recall_graph/necessity/coverage гарантирована. `pass` по таким числам
    утверждал бы о системе то, чего измерение не проверяло.
    """
    baseline = {
        "retrieval": {"recall_at_k": 0.5},
        "generation": {"groundedness": 0.7, "coverage": 0.6},
    }
    target = {
        "retrieval": {"recall_at_k": 0.9},
        "generation": {"groundedness": 0.8, "coverage": 0.7},
        "ingest_quality": _ingest_quality(
            llm_layer_dropped_documents=3,
            llm_layer_lost_entities=412,
            llm_layer_lost_edges=63,
            loss=True,
        ),
    }

    report = lift_report(baseline, target)

    assert report["verdict"] == "invalid"
    assert any("потерян LLM-слой" in reason for reason in report["verdict_reasons"])
    # причина обязана называть размер: «что-то сломалось» не помогает разбираться
    assert any("3 док." in reason and "412" in reason for reason in report["verdict_reasons"])


def test_lift_report_invalidates_when_baseline_lost_llm_layer() -> None:
    """Потеря в baseline смещает базу сравнения так же, как потеря в target.

    Проверяется обе стороны пары: незаметная потеря в baseline делает delta
    бессмысленным ровно так же, как потеря в target.
    """
    baseline = {
        "retrieval": {"recall_at_k": 0.5},
        "generation": {"groundedness": 0.7, "coverage": 0.6},
        "ingest_quality": _ingest_quality(
            llm_layer_dropped_documents=1, llm_layer_lost_entities=5, loss=True
        ),
    }
    target = {
        "retrieval": {"recall_at_k": 0.9},
        "generation": {"groundedness": 0.8, "coverage": 0.7},
    }

    report = lift_report(baseline, target)

    assert report["verdict"] == "invalid"
    assert any("baseline" in reason for reason in report["verdict_reasons"])


def test_lift_report_keeps_verdict_when_degraded_without_loss() -> None:
    """Деградация без потери слоя прогон не инвалидирует.

    Профиль не загрузился — экстракция не запускалась, терять было нечего, векторная
    ось цела. Блокировать вердикт из-за этого — значит научить читателей игнорировать
    предупреждение целиком, включая настоящие случаи.
    """
    baseline = {
        "retrieval": {"recall_at_k": 0.5},
        "generation": {"groundedness": 0.7, "coverage": 0.6},
    }
    target = {
        "retrieval": {"recall_at_k": 0.8},
        "generation": {"groundedness": 0.8, "coverage": 0.7},
        "ingest_quality": _ingest_quality(
            enrichment_degraded_documents=4, loss=False
        ),
    }

    report = lift_report(baseline, target)

    assert report["verdict"] == "pass"
    assert report["verdict_reasons"] == []


def test_lift_report_names_why_verdict_is_invalid() -> None:
    """`invalid` без причины — это «разбирайтесь сами», и его не читают как причину.

    Проверяется на существующем пути (degraded_questions), а не только на новом: если
    причина вводится ради одного случая, она рано или поздно теряется на старом.
    """
    baseline = {
        "retrieval": {"recall_at_k": 0.5},
        "generation": {"groundedness": 0.7, "coverage": 0.6},
    }
    target = {
        "retrieval": {"recall_at_k": 0.8},
        "generation": {"groundedness": 0.8, "coverage": 0.7},
        "graph_contribution": {"degraded_questions": 2},
    }

    report = lift_report(baseline, target)

    assert report["verdict"] == "invalid"
    assert report["verdict_reasons"] == ["вопросы с недоступным графовым вкладом: 2"]


def test_lift_report_missing_generation_metrics() -> None:
    """Судья активен, а метрик нет — это дефект прогона, а не успех.

    Раньше здесь стояло ``verdict == "pass"`` с комментарием «0>=0»: неизмеренные
    groundedness/coverage подставлялись нулём, и сломанный прогон отчитывался зелёным.
    Теперь ноль и «не измерено» различаются, а отсутствие метрик при живом судье
    даёт invalid.
    """
    report = lift_report({"retrieval": {}}, {"retrieval": {"recall_at_k": 0.8}})
    assert report["verdict"] == "invalid"
    assert report["delta"]["groundedness"] is None
    assert report["delta"]["coverage"] is None


def test_lift_report_delta_is_null_without_judge() -> None:
    """Без судьи дельта по метрикам уровня 2 — null, а не 0.0 (валютное правило)."""
    report = lift_report(
        {"retrieval": {"recall_at_k": 0.4}},
        {"retrieval": {"recall_at_k": 0.7}},
        judge_active=False,
    )
    assert report["verdict"] == "n/a"
    assert report["delta"]["groundedness"] is None
    assert report["delta"]["coverage"] is None
    assert report["delta"]["recall_at_k"] == pytest.approx(0.3)


def test_graph_contribution_vector_only_covers_all() -> None:
    """Векторная ось полностью перекрывает графовый golden -> вклад 0."""
    golden = {"s://g1", "s://g2"}
    m = graph_contribution(
        ["s://g1"],
        ["s://g1", "s://g2"],
        golden,
        golden_graph_evidence=True,
    )
    assert m["mode"] == "measured"
    assert m["recall_graph"] == 0.5
    assert m["recall_vector"] == 1.0
    assert m["necessity"] == 0.0
    assert m["delta_recall"] == 0.0


def test_graph_contribution_necessity_without_graph_evidence() -> None:
    """Структурный вопрос: только graph experiment покрывает golden."""
    golden = {"s://g1"}
    m = graph_contribution(
        ["s://g1"],
        ["s://vector-only"],
        golden,
        golden_graph_evidence=True,
    )
    assert m["necessity"] == 1.0
    assert m["evidence_recall_graph"] == 1.0
    assert m["delta_recall"] == 1.0


def test_graph_contribution_mixed_axes_dedup() -> None:
    """Один и тот же url на обеих осях не завышает ни одну из метрик."""
    golden = {"s://shared", "s://graph-only", "s://vector-only"}
    m = graph_contribution(
        ["s://shared", "s://graph-only"],
        ["s://shared", "s://vector-only"],
        golden,
        golden_graph_evidence=True,
    )
    assert m["recall_graph"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["recall_vector"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["necessity"] == pytest.approx(1 / 3, abs=1e-3)  # покрыт только графовой осью
    assert m["delta_recall"] == pytest.approx(1 / 3, abs=1e-3)  # гибрид покрывает всё


def test_graph_contribution_not_measured_without_evidence() -> None:
    """Без golden_graph_evidence вклад не интерпретируем (design.md:79)."""
    m = graph_contribution(
        ["s://g1"],
        [],
        {"s://g1"},
        golden_graph_evidence=False,
    )
    assert m["mode"] == "not_measured"
    assert m["evidence_recall_graph"] is None
    assert m["necessity"] == 0.0
    assert m["delta_recall"] == 0.0