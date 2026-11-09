"""Normalizer canary: compare LLM normalization with the deterministic one."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from llm_degradation.config import Thresholds
from llm_degradation.metrics import jaccard, normalize_url, token_set
from llm_degradation.models import CanaryResult, NormalizedInput
from llm_degradation.ollama import LabOllamaClient
from llm_degradation.prompts import load_prompt, render_prompt
from llm_degradation.runner import SeriesOptions

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ID_LINE = re.compile(r"^#{2,6}\s+([A-Za-zА-Яа-я]+-\d+)\s*$")
URL_LINE = re.compile(r"^\*\*(?:URL|Url|url):\*\*\s*(.+?)\s*$", re.MULTILINE)
TITLE_LINE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class CanonicalDocument:
    title: str
    url: str | None
    ids: list[str]
    bodies: dict[str, str]
    parse_error: str | None


@dataclass(frozen=True)
class CanaryRun:
    results: list[CanaryResult]
    verdict: str
    reasons: list[str]


def canary_system(prompt_dir: Path | None = None) -> str:
    """Return the canary system prompt from ``prompts/canary-system.txt``."""
    return load_prompt("canary-system.txt", prompt_dir)


def canary_prompt(normalized: NormalizedInput, prompt_dir: Path | None = None) -> str:
    prefix = normalized.settings.id_prefix
    template = load_prompt("canary-user.txt", prompt_dir)
    body = render_prompt(
        template,
        {
            "prefix": prefix,
            "heading": normalized.settings.requirements_heading,
            "text": normalized.text,
        },
    )
    return body + "\n"


def parse_canonical(text: str, prefix: str = "BR") -> CanonicalDocument:
    lines = text.splitlines()
    title_match = TITLE_LINE.search(text)
    url_match = URL_LINE.search(text)
    title = title_match.group(1).strip() if title_match else ""
    url = url_match.group(1).strip() if url_match else None

    id_pattern = re.compile(rf"^#{{2,6}}\s+({re.escape(prefix)}-\d+)\s*$")
    ids: list[str] = []
    bodies: dict[str, str] = {}
    current: str | None = None
    body_lines: list[str] = []

    def flush() -> None:
        if current is not None:
            bodies[current] = " ".join(line.strip() for line in body_lines if line.strip())

    for line in lines:
        heading = HEADING.match(line)
        if heading:
            if current is not None:
                flush()
                current = None
                body_lines = []
            match = id_pattern.match(line)
            if match:
                current = match.group(1)
                ids.append(current)
            continue
        if current is not None:
            body_lines.append(line)
    if current is not None:
        flush()

    parse_error: str | None = None
    if not title and not ids:
        parse_error = "no title and no requirement headings found"
    return CanonicalDocument(
        title=title,
        url=url,
        ids=ids,
        bodies=bodies,
        parse_error=parse_error,
    )


def compare_canary_results(
    llm_output: str,
    deterministic_output: str,
    *,
    thresholds: Thresholds,
    prefix: str = "BR",
) -> CanaryResult:
    llm = parse_canonical(llm_output, prefix)
    det = parse_canonical(deterministic_output, prefix)

    if llm.parse_error:
        return CanaryResult(
            verdict="fail",
            parse_error=llm.parse_error,
            llm_canonical=llm_output[:300],
            det_canonical=deterministic_output[:300],
            raw=llm_output,
        )

    llm_ids = llm.ids
    det_ids = det.ids
    llm_bodies = llm.bodies
    det_bodies = det.bodies

    ids_exact = llm_ids == det_ids
    count_exact = len(llm_ids) == len(det_ids)
    extra_ids = [item for item in llm_ids if item not in det_ids]
    missing_ids = [item for item in det_ids if item not in llm_ids]

    llm_url = normalize_url(llm.url) if llm.url else ""
    det_url = normalize_url(det.url) if det.url else ""
    url_exact = llm_url == det_url

    title_overlap = jaccard(token_set(llm.title), token_set(det.title))
    body_scores = [
        jaccard(token_set(llm_bodies.get(item, "")), token_set(det_bodies.get(item, "")))
        for item in det_ids
    ]
    body_mean = sum(body_scores) / len(body_scores) if body_scores else 1.0
    body_min = min(body_scores) if body_scores else 1.0

    reasons: list[str] = []
    if not ids_exact:
        reasons.append("requirement ids differ (set or order)")
    if not count_exact:
        reasons.append(f"id count differs: llm={len(llm_ids)}, deterministic={len(det_ids)}")
    if not url_exact:
        reasons.append(f"url differs: llm={llm_url!r}, deterministic={det_url!r}")
    if title_overlap < thresholds.canary_title_overlap_min:
        reasons.append(
            f"title_overlap {title_overlap:.2f} < {thresholds.canary_title_overlap_min:.2f}"
        )
    if body_mean < thresholds.canary_body_overlap_mean_min:
        reasons.append(
            f"body_overlap_mean {body_mean:.2f} < {thresholds.canary_body_overlap_mean_min:.2f}"
        )
    if body_min < thresholds.canary_body_overlap_min:
        reasons.append(
            f"body_overlap_min {body_min:.2f} < {thresholds.canary_body_overlap_min:.2f}"
        )

    if reasons:
        verdict = "fail"
    elif body_mean < 0.95:
        verdict = "warn"
    else:
        verdict = "pass"

    return CanaryResult(
        verdict=verdict,
        parse_error=None,
        ids_exact=ids_exact,
        count_exact=count_exact,
        url_exact=url_exact,
        title_overlap=round(title_overlap, 4),
        body_overlap_mean=round(body_mean, 4),
        body_overlap_min=round(body_min, 4),
        extra_ids=extra_ids,
        missing_ids=missing_ids,
        llm_canonical=llm_output[:300],
        det_canonical=deterministic_output[:300],
        raw=llm_output,
    )


def run_canary(
    client: LabOllamaClient,
    normalized: NormalizedInput,
    *,
    model: str,
    runs: int = 1,
    options: SeriesOptions,
    thresholds: Thresholds,
    on_run: Callable[[int, int, CanaryResult], None] | None = None,
    probe_prompt_dir: Path | None = None,
) -> CanaryRun:
    system = canary_system(probe_prompt_dir)
    user = canary_prompt(normalized, probe_prompt_dir)
    results: list[CanaryResult] = []
    for index in range(1, runs + 1):
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
        result = compare_canary_results(
            response.content,
            normalized.text,
            thresholds=thresholds,
            prefix=normalized.settings.id_prefix,
        )
        results.append(result)
        if on_run is not None:
            on_run(index, runs, result)

    verdict = _worst([item.verdict for item in results])
    reasons: list[str] = []
    if verdict == "fail":
        reasons = [
            f"run {index}: parse_error={item.parse_error}"
            if item.parse_error
            else f"run {index}: canary metrics below thresholds"
            for index, item in enumerate(results, start=1)
            if item.verdict == "fail"
        ]
    return CanaryRun(results=results, verdict=verdict, reasons=reasons)


def _worst(verdicts: Sequence[str]) -> str:
    order = {"pass": 0, "warn": 1, "fail": 2}
    return max(verdicts, key=lambda item: order.get(item, 2), default="fail")
