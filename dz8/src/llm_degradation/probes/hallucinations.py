"""Hallucination probes.

Every probe is deterministic: the verdict is derived from the model text and
the normalized source, never from a judge model. A probe fails if at least one
of its runs fails, so a single hallucination is not averaged away.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_testgen.models import ArtifactType
from pydantic import BaseModel, ConfigDict, Field

from llm_degradation.config import Thresholds
from llm_degradation.metrics import URL_PATTERN, normalize_url
from llm_degradation.models import NormalizedInput, ProbeResult, VerdictInfo
from llm_degradation.normalizer_bridge import build_prompt, load_normalized
from llm_degradation.ollama import LabOllamaClient
from llm_degradation.prompts import load_prompt, render_prompt
from llm_degradation.runner import SeriesOptions, run_series, source_urls
from llm_degradation.util import write_json_atomic, write_text_atomic

DEFAULT_PROBES_PATH = Path("probes/hallucinations.json")
DEFAULT_MARKERS_PATH = Path("probes/refusal_markers.txt")

CURRENCY_PATTERN = re.compile(
    r"\d[\d\s.,]*\s*(?:₽|руб(?:\.|лей|ля|ль)?|rub(?:les)?|ruble|\$|€|usd|eur)",
    re.IGNORECASE,
)
NUMBER_PATTERN = re.compile(r"\d+")
NEGATIONS = ("нет", "не ", "отсутств", "не должен", "without", "no ")

ANALYST_SYSTEM_FILE = "analyst-system.txt"
ANALYST_USER_FILE = "analyst-user.txt"
DEFAULT_QUESTION_FILE = "hallucination-default-question.txt"


def analyst_system(prompt_dir: Path | None = None) -> str:
    """Return the analyst system prompt from ``prompts/analyst-system.txt``."""
    return load_prompt(ANALYST_SYSTEM_FILE, prompt_dir)


class ProbeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    input: str
    prompt: str | None = None
    prompt_file: str | None = None
    expected: dict[str, Any] = Field(default_factory=dict)
    forbidden_terms: list[str] = Field(default_factory=list)
    critical: bool = False
    checks: list[str] = Field(default_factory=list)
    artifact_type: str | None = None
    count: int | None = None
    allow_negated: bool = False


class ProbeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    probes: list[ProbeDefinition]


@dataclass(frozen=True)
class HallucinationResult:
    probes: list[ProbeResult]
    summary: dict[str, Any]
    verdict: VerdictInfo


def load_probes(path: Path) -> list[ProbeDefinition]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read probes file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in probes file {path}: {exc}") from exc
    probe_set = ProbeSet.model_validate(raw)
    if probe_set.schema_version != 1:
        raise ValueError(f"unsupported probes schema_version: {probe_set.schema_version}")
    if not probe_set.probes:
        raise ValueError("probes file has no probes")
    return probe_set.probes


def load_refusal_markers(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read refusal markers {path}: {exc}") from exc
    markers = [line.strip().casefold() for line in lines if line.strip()]
    if not markers:
        raise ValueError(f"refusal markers file is empty: {path}")
    return markers


def run_hallucinations(
    client: LabOllamaClient,
    probes: Sequence[ProbeDefinition],
    *,
    model: str,
    root: Path,
    golden_dir: Path,
    options: SeriesOptions,
    runs: int,
    strict: bool,
    thresholds: Thresholds,
    normalizer_config: Path | None = None,
    prompt_dir: Path | None = None,
    probe_prompt_dir: Path | None = None,
    runs_dir: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> HallucinationResult:
    markers = load_refusal_markers(golden_dir / DEFAULT_MARKERS_PATH)
    results: list[ProbeResult] = []
    total_runs = 0

    for position, probe in enumerate(probes, start=1):
        input_path = _resolve_probe_path(probe.input, root)
        normalized = load_normalized(input_path, normalizer_config=normalizer_config)
        evidence = ""
        forbidden_hits: list[str] = []
        details: list[str] = []
        failed_runs = 0

        for run_index in range(1, runs + 1):
            probe_dir = (
                runs_dir / "probes" / probe.id / f"run-{run_index:02d}"
                if runs_dir is not None
                else None
            )
            if probe.artifact_type:
                failed, run_evidence, run_forbidden, run_details = _run_artifact_probe(
                    client,
                    probe,
                    normalized,
                    model=model,
                    options=options,
                    prompt_dir=prompt_dir,
                    probe_dir=probe_dir,
                )
            else:
                failed, run_evidence, run_forbidden, run_details = _run_text_probe(
                    client,
                    probe,
                    normalized,
                    markers=markers,
                    model=model,
                    options=options,
                    probe_dir=probe_dir,
                    probe_prompt_dir=probe_prompt_dir,
                )
            total_runs += 1
            if failed:
                failed_runs += 1
                if not evidence:
                    evidence = run_evidence
                forbidden_hits.extend(item for item in run_forbidden if item not in forbidden_hits)
                details.extend(item for item in run_details if item not in details)

        status = "fail" if failed_runs else "pass"
        results.append(
            ProbeResult(
                id=probe.id,
                kind=probe.kind,
                status=status,
                critical=probe.critical or strict,
                evidence=evidence[:200],
                forbidden_hits=forbidden_hits,
                details=details,
                runs=runs,
                failed_runs=failed_runs,
            )
        )
        if progress is not None:
            progress(position, len(probes), probe.id)

    threshold = 0.0 if strict else thresholds.hallucination_rate_max
    failed_probes = [item for item in results if item.failed_runs > 0]
    rate = len(failed_probes) / len(results) if results else 0.0
    critical_failures = [item.id for item in failed_probes if item.critical]

    reasons: list[str] = []
    if critical_failures:
        status = "fail"
        reasons.append("critical probes failed: " + ", ".join(critical_failures))
    elif rate > threshold:
        status = "fail"
        reasons.append(f"hallucination_rate {rate:.2f} > {threshold:.2f}")
    elif rate > 0:
        status = "warn"
        reasons.append(f"hallucination_rate {rate:.2f} <= {threshold:.2f}")
    else:
        status = "pass"

    summary = {
        "probes_total": len(results),
        "failed_probes": len(failed_probes),
        "hallucination_rate": round(rate, 4),
        "failed_runs_total": sum(item.failed_runs for item in results),
        "runs_total": total_runs,
        "critical_failures": critical_failures,
        "strict": strict,
        "hallucination_rate_max": threshold,
    }
    verdict = VerdictInfo(
        status=status,
        reasons=reasons,
        thresholds={
            "hallucination_rate_max": threshold,
            "runs_per_probe": float(runs),
        },
    )
    return HallucinationResult(probes=results, summary=summary, verdict=verdict)


def _run_artifact_probe(
    client: LabOllamaClient,
    probe: ProbeDefinition,
    normalized: NormalizedInput,
    *,
    model: str,
    options: SeriesOptions,
    prompt_dir: Path | None,
    probe_dir: Path | None,
) -> tuple[bool, str, list[str], list[str]]:
    artifact_type = ArtifactType(probe.artifact_type or "testcases")
    count = probe.count or 5
    prompt = build_prompt(
        normalized,
        artifact_type,
        count,
        prompt_dir=prompt_dir,
    )
    records = run_series(
        client,
        normalized,
        prompt,
        artifact_type=artifact_type,
        count=count,
        options=SeriesOptions(
            runs=1,
            temperature=0.0,
            seed=options.seed,
            num_ctx=options.num_ctx,
            num_predict=options.num_predict,
            max_retries=0,
        ),
        prompt_dir=prompt_dir,
        runs_dir=probe_dir.parent if probe_dir is not None else None,
    )
    record = records[0]
    metrics = record.metrics
    details: list[str] = []
    forbidden: list[str] = []

    if record.error:
        details.append(record.error)
        return True, _quote(record.error), forbidden, details

    passed = True
    for check in probe.checks:
        if check == "no_execution_claims":
            if metrics.claims_execution_results:
                passed = False
                forbidden.extend(metrics.claims_markers)
                details.append(
                    "execution results claimed without source data: "
                    + ", ".join(metrics.claims_markers)
                )
        elif check in {"br_ids_subset", "no_invented_br"}:
            if metrics.br_invented:
                passed = False
                forbidden.extend(metrics.br_invented)
                details.append("invented requirement ids: " + ", ".join(metrics.br_invented))
        elif check in {"tc_ids_known", "no_invented_tc"}:
            if metrics.tc_ids:
                passed = False
                forbidden.extend(metrics.tc_ids)
                details.append("test case ids without an index: " + ", ".join(metrics.tc_ids))
        elif check == "validator_pass":
            if not metrics.validator_pass:
                passed = False
                details.extend(metrics.validator_issues)
        elif check == "no_invented_url":
            if metrics.urls_invented:
                passed = False
                forbidden.extend(metrics.urls_invented)
                details.append("invented urls: " + ", ".join(metrics.urls_invented))
        else:
            details.append(f"unknown check ignored: {check}")

    evidence = _quote(", ".join(details)) if details else ""
    return not passed, evidence, forbidden, details


def _run_text_probe(
    client: LabOllamaClient,
    probe: ProbeDefinition,
    normalized: NormalizedInput,
    *,
    markers: Sequence[str],
    model: str,
    options: SeriesOptions,
    probe_dir: Path | None,
    probe_prompt_dir: Path | None = None,
) -> tuple[bool, str, list[str], list[str]]:
    if probe.prompt_file:
        question = load_prompt(probe.prompt_file, probe_prompt_dir)
    elif probe.prompt:
        question = probe.prompt
    else:
        question = load_prompt(DEFAULT_QUESTION_FILE, probe_prompt_dir)
    template = load_prompt(ANALYST_USER_FILE, probe_prompt_dir)
    user = render_prompt(template, {"question": question, "text": normalized.text}) + "\n"
    system = analyst_system(probe_prompt_dir)
    response = client.chat_messages(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        seed=options.seed,
        num_ctx=options.num_ctx,
        num_predict=options.num_predict,
        model=model,
    )
    raw = response.content
    if probe_dir is not None:
        write_json_atomic(
            probe_dir / "request.json",
            {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            },
        )
        write_text_atomic(probe_dir / "response.txt", raw)

    passed, forbidden, details = _evaluate_text_probe(probe, raw, normalized, markers)
    evidence = _evidence(raw, forbidden, details)
    return not passed, evidence, forbidden, details


def _evaluate_text_probe(
    probe: ProbeDefinition,
    raw: str,
    normalized: NormalizedInput,
    markers: Sequence[str],
) -> tuple[bool, list[str], list[str]]:
    if not raw.strip():
        return False, [], ["empty response"]

    forbidden: list[str] = []
    details: list[str] = []
    lowered = raw.casefold()
    has_refusal = any(marker in lowered for marker in markers)

    if probe.kind == "count":
        expected = probe.expected.get("number")
        numbers = [int(item) for item in NUMBER_PATTERN.findall(raw)]
        if expected is None or int(expected) not in numbers:
            details.append(f"expected number {expected!r}, got numbers {numbers[:5]}")
            return False, forbidden, details
        return True, forbidden, details

    if probe.kind == "missing_url":
        allowed = {normalize_url(item) for item in source_urls(normalized)}
        urls = [normalize_url(item) for item in URL_PATTERN.findall(raw)]
        invented = sorted({item for item in urls if item and item not in allowed})
        if invented:
            forbidden.extend(invented)
            details.append("invented urls: " + ", ".join(invented))
            return False, forbidden, details
        return True, forbidden, details

    if probe.kind == "numeric":
        currencies = CURRENCY_PATTERN.findall(raw)
        if currencies:
            forbidden.extend(currencies)
            details.append("invented currency amounts: " + ", ".join(currencies))
        if not has_refusal:
            details.append("no refusal marker in the answer")
        return not details, forbidden, details

    if probe.kind in {"missing_feature", "entity"}:
        hits = _forbidden_hits(raw, probe.forbidden_terms, probe.allow_negated)
        if hits:
            forbidden.extend(hits)
            details.append("forbidden terms found: " + ", ".join(hits))
        if not has_refusal:
            details.append("no refusal marker in the answer")
        return not details, forbidden, details

    hits = _forbidden_hits(raw, probe.forbidden_terms, probe.allow_negated)
    if hits:
        forbidden.extend(hits)
        details.append("forbidden terms found: " + ", ".join(hits))
    return not details, forbidden, details


def _forbidden_hits(text: str, terms: Sequence[str], allow_negated: bool) -> list[str]:
    lowered = text.casefold()
    hits: list[str] = []
    for term in terms:
        needle = term.casefold()
        start = 0
        while True:
            index = lowered.find(needle, start)
            if index < 0:
                break
            if not (allow_negated and _negated(lowered, index)):
                hits.append(term)
                break
            start = index + len(needle)
    return hits


def _negated(text: str, index: int) -> bool:
    window = text[max(0, index - 80) : index]
    return any(marker in window for marker in NEGATIONS)


def _evidence(raw: str, forbidden: Sequence[str], details: Sequence[str]) -> str:
    lowered = raw.casefold()
    for term in forbidden:
        index = lowered.find(term.casefold())
        if index >= 0:
            start = max(0, index - 60)
            return raw[start : index + len(term) + 60].strip()
    if details:
        return _quote("; ".join(details))
    return _quote(raw)


def _quote(text: str) -> str:
    return " ".join(text.split())[:200]


def _resolve_probe_path(raw: str, root: Path) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else root / candidate
