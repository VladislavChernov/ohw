"""Eval-метрики (ADR-015): Retrieval@K, generation metrics, lift-report.

Метрики:
- retrieval_metrics: Recall@K, Precision@K, MRR@K, nDCG@K по golden_sources.
- generation_metrics: groundedness, coverage, hallucination_rate через LLM-judge.
- lift_report: delta и verdict (target >= baseline по groundedness/coverage).
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any


def retrieval_metrics(retrieved: list[str], golden: set[str], k: int = 5) -> dict[str, float]:
    """Метрики ретрива (ADR-015, K по умолчанию 5).

    retrieved — упорядоченный список source_url из done["sources"].
    golden — множество релевантных source_url.

    Дубли source_url между осями (один источник принесён и графовой, и векторной
    осью, т.к. build_sources дедуплицирует по паре (source_url, axis)) устраняются
    здесь по первому вхождению: метрика считается по уникальным URL, иначе Recall/
    Precision/nDCG способны превысить 1.0 и сместить target-ветку (design.md §2,
    задача 2.3 «смешение осей корректно дедуплицируется»).
    """
    retrieved = list(dict.fromkeys(retrieved))
    top = retrieved[:k]
    relev = [url for url in top if url in golden]
    count = len(relev)

    # Recall@K
    recall = count / len(golden) if golden else 0.0

    # Precision@K
    precision = count / k if k else 0.0

    # MRR@K: reciprocal rank первого релевантного
    mrr = 0.0
    for i, url in enumerate(top, 1):
        if url in golden:
            mrr = 1.0 / i
            break

    # nDCG@K: бинарная релевантность
    dcg = sum(1.0 / math.log2(i + 1) for i, url in enumerate(top, 1) if url in golden)
    ideal_hits = min(len(golden), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    ndcg = dcg / idcg if idcg else 0.0

    return {
        "recall_at_k": round(recall, 4),
        "precision_at_k": round(precision, 4),
        "mrr_at_k": round(mrr, 4),
        "ndcg_at_k": round(ndcg, 4),
        "k": k,
    }


def generation_metrics(
    answer: str,
    golden_facts: list[str],
    judge: Any,
    k: int = 5,
) -> dict[str, Any]:
    """Метрики генерации (ADR-015): groundedness, coverage, hallucination_rate.

    judge — объект с методом generate(prompt, system) -> Iterator[str].
    Решение, вызывать ли эти метрики (есть ли реальный LLM-judge), принимает
    раннер по env EVAL_LLM_ADAPTER — здесь логика чисто вычислительная.
    """
    answer_stripped = answer.strip()
    if not answer_stripped or not golden_facts:
        return {"groundedness": 0.0, "coverage": 0.0, "hallucination_rate": 1.0, "mode": "empty"}

    # Groundedness: доля утверждений ответа, подтверждаемых контекстом
    claims_prompt = (
        "Разбей ответ на отдельные фактические утверждения. "
        "Каждое утверждение — одна строка, начни с '- '. Ответ:\n" + answer
    )
    raw_claims = "".join(judge.generate(claims_prompt, system="Разбей текст на факты."))
    claims = [line.lstrip("- ").strip() for line in raw_claims.splitlines() if line.strip().startswith("-")]

    grounded_count = 0
    for claim in claims[:20]:  # ограничиваем чтобы не ломать контекст judge'а
        check_prompt = (
            "Контекст:\n" + "\n".join(golden_facts) + "\n\n"
            "Утверждение: " + claim + "\n\n"
            "Подтверждается ли утверждение контекстом? Ответь ТОЛЬКО yes или no."
        )
        verdict = "".join(judge.generate(check_prompt, system="Отвечай только yes или no."))
        if "yes" in verdict.lower().strip()[:5]:
            grounded_count += 1
    groundedness = grounded_count / len(claims) if claims else 0.0

    # Coverage: доля golden_facts, покрытых ответом
    covered_count = 0
    for fact in golden_facts[:20]:
        check_prompt = (
            "Ответ:\n" + answer + "\n\n"
            "Факт: " + fact + "\n\n"
            "Содержится ли этот факт в ответе? Ответь ТОЛЬКО yes или no."
        )
        verdict = "".join(judge.generate(check_prompt, system="Отвечай только yes или no."))
        if "yes" in verdict.lower().strip()[:5]:
            covered_count += 1
    coverage = covered_count / len(golden_facts) if golden_facts else 0.0

    # Hallucination_rate: доля утверждений ответа, НЕ подтверждаемых контекстом
    hallucination_rate = 1.0 - groundedness

    return {
        "groundedness": round(groundedness, 4),
        "coverage": round(coverage, 4),
        "hallucination_rate": round(hallucination_rate, 4),
        "mode": "judge",
    }


def graph_contribution(
    retrieved_graph: list[str],
    retrieved_vector: list[str],
    golden: set[str],
    *,
    golden_graph_evidence: bool,
) -> dict[str, Any]:
    """Вклад графовой оси (design.md §3): вычислительная метрика, без judge.

    По одному вопросу: упорядоченные списки source_url по осям (`axis=graph` /
    `axis=vector` из `done.sources`). Срез целевых вопросов — те, где
    `golden_graph_evidence=True`; на остальных вклад «не измеряем»
    (`mode="not_measured"`, necessity/delta обнулены — design.md:79 «не
    интерпретировать»).

    - recall_graph / recall_vector — Recall по golden в пределах оси;
    - evidence_recall_graph — Recall графовой оси на графовом срезе;
    - necessity — доля golden-источников, покрытых только графовой осью;
    - delta_recall — recall(обе оси) − recall(vector-only) по срезу.
    """

    def _recall(covered: set[str]) -> float:
        return len(covered & golden) / len(golden) if golden else 0.0

    graph_set = set(retrieved_graph)
    vector_set = set(retrieved_vector)
    recall_graph = _recall(graph_set)
    recall_vector = _recall(vector_set)
    recall_hybrid = _recall(graph_set | vector_set)
    delta_recall = recall_hybrid - recall_vector
    if not golden_graph_evidence:
        return {
            "recall_graph": round(recall_graph, 4),
            "recall_vector": round(recall_vector, 4),
            "evidence_recall_graph": None,
            "necessity": 0.0,
            "delta_recall": 0.0,
            "mode": "not_measured",
        }
    graph_only = (graph_set - vector_set) & golden
    necessity = len(graph_only) / len(golden) if golden else 0.0
    return {
        "recall_graph": round(recall_graph, 4),
        "recall_vector": round(recall_vector, 4),
        "evidence_recall_graph": round(recall_graph, 4),
        "necessity": round(necessity, 4),
        "delta_recall": round(delta_recall, 4),
        "mode": "measured",
    }


def lift_report(
    baseline: dict[str, Any],
    target: dict[str, Any],
    revision: str | None = None,
    *,
    judge_active: bool = True,
) -> dict[str, Any]:
    """Lift-отчёт ADR-015: delta и verdict (валютное правило).

    baseline/target — агрегированные метрики одного прогона:
    {"retrieval": {...}, "generation": {...}, "graph_contribution": {...}}.
    Verdict: pass, если target.groundedness >= baseline.groundedness И
    target.coverage >= baseline.coverage. Без судьи (`judge_active=False`,
    --no-judge/--retrieval-only) verdict = "n/a": groundedness/coverage не
    вычислены, валютное правило неприменимо (design.md §6).
    """
    gen_b = baseline.get("generation", {})
    gen_t = target.get("generation", {})

    def _safe(val: Any) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        return 0.0

    def _gc(mode: dict[str, Any], key: str) -> float:
        return _safe((mode.get("graph_contribution") or {}).get(key))

    delta = {
        "recall_at_k": _safe(target.get("retrieval", {}).get("recall_at_k"))
        - _safe(baseline.get("retrieval", {}).get("recall_at_k")),
        "groundedness": _safe(gen_t.get("groundedness")) - _safe(gen_b.get("groundedness")),
        "coverage": _safe(gen_t.get("coverage")) - _safe(gen_b.get("coverage")),
        "necessity": _gc(target, "necessity") - _gc(baseline, "necessity"),
        "delta_recall": _gc(target, "delta_recall") - _gc(baseline, "delta_recall"),
        "evidence_recall_graph": _gc(target, "evidence_recall_graph")
        - _gc(baseline, "evidence_recall_graph"),
    }

    target_graph = target.get("graph_contribution") or {}
    degraded_questions = target_graph.get("degraded_questions", 0)
    if isinstance(degraded_questions, (int, float)) and degraded_questions > 0:
        verdict = "invalid"
    elif not judge_active:
        verdict = "n/a"
    else:
        verdict = "pass" if delta["groundedness"] >= 0 and delta["coverage"] >= 0 else "fail"

    return {
        "baseline": baseline,
        "target": target,
        "delta": {k: round(v, 4) for k, v in delta.items()},
        "verdict": verdict,
        "revision": revision,
        "run_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(UTC).isoformat(),
    }