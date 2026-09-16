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
    """
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


def lift_report(
    baseline: dict[str, Any],
    target: dict[str, Any],
    revision: str | None = None,
) -> dict[str, Any]:
    """Lift-отчёт ADR-015: delta и verdict (валютное правило).

    baseline/target — агрегированные метрики одного прогона:
    {"retrieval": {...}, "generation": {...}, "metadata": {...}}.
    Verdict: pass, если target.groundedness >= baseline.groundedness И
    target.coverage >= baseline.coverage.
    """
    gen_b = baseline.get("generation", {})
    gen_t = target.get("generation", {})

    def _safe(val: Any) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        return 0.0

    delta = {
        "recall_at_k": _safe(target.get("retrieval", {}).get("recall_at_k"))
        - _safe(baseline.get("retrieval", {}).get("recall_at_k")),
        "groundedness": _safe(gen_t.get("groundedness")) - _safe(gen_b.get("groundedness")),
        "coverage": _safe(gen_t.get("coverage")) - _safe(gen_b.get("coverage")),
    }

    # Валютное правило ADR-015
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