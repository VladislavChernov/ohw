from __future__ import annotations

from typing import Any, Iterable

VERDICTS = ("blocked", "bypass", "mixed", "unknown", "error", "not_applicable")
COUNTED_VERDICTS = ("blocked", "bypass", "mixed", "unknown", "error")


def percentile(values: Iterable[float], percentage: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentage
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def scenario_verdict(verdicts: list[str]) -> str:
    if len(verdicts) < 3:
        return "unknown"
    if "bypass" in verdicts and "blocked" in verdicts:
        return "mixed"
    if "bypass" in verdicts:
        return "bypass"
    if "error" in verdicts:
        return "error"
    if "blocked" in verdicts:
        return "blocked"
    if "unknown" in verdicts:
        return "unknown"
    if "not_applicable" in verdicts:
        return "not_applicable"
    return "unknown"


def summarize_results(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        scenario = result.get("scenario")
        if not isinstance(scenario, str):
            continue
        grouped.setdefault(scenario, []).append(result)

    summaries: list[dict[str, Any]] = []
    for scenario in sorted(grouped):
        rows = sorted(grouped[scenario], key=lambda item: item.get("run", 0))
        verdicts = [str(row.get("verdict", "unknown")) for row in rows]
        counts = {verdict: verdicts.count(verdict) for verdict in COUNTED_VERDICTS}
        latency_values = [
            float(row["latency_ms"])
            for row in rows
            if isinstance(row.get("latency_ms"), (int, float))
        ]
        first = rows[0]
        summaries.append(
            {
                "scenario": scenario,
                "owasp": first.get("owasp", "unknown"),
                "title": first.get("title", scenario),
                "preset": first.get("preset"),
                "n": len(rows),
                "counts": counts,
                "verdict": scenario_verdict(verdicts),
                "latency_ms": {
                    "p50": percentile(latency_values, 0.50),
                    "p95": percentile(latency_values, 0.95),
                },
                "cpu": first.get("cpu"),
                "ram_mb": first.get("ram_mb"),
            }
        )
    return summaries
