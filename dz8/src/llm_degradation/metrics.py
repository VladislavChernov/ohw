"""Deterministic artifact metrics.

The function is pure: no LLM, no network. Validation is delegated to the Dz2
``ai_testgen.validator`` so the lab and the workbench agree on what a valid
artifact is.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

from ai_testgen.models import ArtifactType, TestCase
from ai_testgen.prompt import (
    COVERAGE_MATRIX_SECTIONS,
    QAREPORT_SECTIONS,
    TESTPLAN_SECTIONS,
)
from ai_testgen.validator import validate, validate_markdown

from .models import ArtifactMetrics

TC_PATTERN = re.compile(r"\bTC-\d{1,4}\b")
URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
HEADING_PATTERN = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$")
LIST_MARKER_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*$")

CLAIM_MARKERS = (
    "пройдено",
    "провалено",
    "успешно",
    "100%",
    "дефектов не обнаружено",
    "тесты выполнены",
    "all tests passed",
    "no defects found",
)
_RESULT_SECTION = re.compile(
    r"(?i)(результат\w*[^\n]{0,40}(?:прогон|тест)|execution results|test run|"
    r"прогон\s*(?:№|#)?\s*\d+)"
)

_TERMINAL = ".!?…:;)]}\"'*`|"


def artifact_metrics(
    artifact_type: ArtifactType,
    raw: str,
    *,
    expected_count: int,
    source_br_ids: Sequence[str],
    source_urls: Sequence[str],
    required_sections: Sequence[str] = (),
    source_text: str = "",
    id_prefix: str = "BR",
) -> ArtifactMetrics:
    text = raw or ""
    stripped = text.strip()
    source_ids = list(dict.fromkeys(source_br_ids))
    source_url_set = {normalize_url(item) for item in source_urls}

    json_valid = _is_json(stripped)
    schema_valid, validator_pass, validator_issues, cases = _validate(
        artifact_type, text, expected_count
    )

    tc_ids = TC_PATTERN.findall(text)
    br_ids = _extract_br_ids(text, id_prefix)
    urls = [normalize_url(item) for item in URL_PATTERN.findall(text)]
    urls = [item for item in urls if item]
    invented_urls = sorted({item for item in urls if item not in source_url_set})

    covered = [item for item in source_ids if item in br_ids]
    missing = [item for item in source_ids if item not in br_ids]
    invented = [item for item in br_ids if item not in source_ids]

    positives = sum(1 for case in cases if case.type.value == "positive")
    negatives = len(cases) - positives
    sections = _section_report(text, list(required_sections))
    claims = _claim_markers(text, artifact_type, source_text)

    return ArtifactMetrics(
        raw_chars=len(text),
        raw_lines=text.count("\n") + 1 if text else 0,
        raw_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        has_code_fence=stripped.startswith("```"),
        json_valid=json_valid,
        schema_valid=schema_valid,
        validator_pass=validator_pass,
        validator_issues=validator_issues,
        cases_total=len(cases),
        positive=positives,
        negative=negatives,
        negative_ratio=round(negatives / len(cases), 4) if cases else 0.0,
        count_matches=len(cases) == expected_count,
        tc_ids=list(tc_ids),
        tc_duplicates=_duplicates(tc_ids),
        br_ids_output=list(br_ids),
        br_ids_source=source_ids,
        br_covered=covered,
        br_missing=missing,
        br_invented=invented,
        br_coverage=round(len(covered) / len(source_ids), 4) if source_ids else 1.0,
        urls_output=urls,
        urls_invented=invented_urls,
        claims_execution_results=bool(claims),
        claims_markers=claims,
        section_headings=sections["headings"],
        title_tokens=sorted(title_tokens(text)),
        required_sections_present=sections["present"],
        required_sections_missing=sections["missing"],
        empty_sections=sections["empty"],
        truncated=_is_truncated(text, json_valid),
        token_count=len(text.split()),
    )


def required_sections_for(artifact_type: ArtifactType) -> tuple[str, ...]:
    """Sections the Dz2 validator requires for a Markdown artifact."""
    if artifact_type is ArtifactType.TESTPLAN:
        return tuple(str(item) for item in TESTPLAN_SECTIONS)
    if artifact_type is ArtifactType.QAREPORT:
        return tuple(str(item) for item in QAREPORT_SECTIONS)
    if artifact_type is ArtifactType.COVERAGEMATRIX:
        return tuple(str(item) for item in COVERAGE_MATRIX_SECTIONS)
    return ()


def normalize_url(url: str) -> str:
    cleaned = url.strip().rstrip(".,;:)]}")
    cleaned = cleaned.split("#", 1)[0]
    return cleaned.rstrip("/")


def _is_json(text: str) -> bool:
    if not text:
        return False
    try:
        json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


def _validate(
    artifact_type: ArtifactType,
    raw: str,
    expected_count: int,
) -> tuple[bool, bool, list[str], list[TestCase]]:
    if artifact_type is ArtifactType.TESTCASES:
        result = validate(raw, expected_count)
        issues = list(result.issues)
        schema_valid = _is_json(raw.strip()) and not any(
            issue.startswith(("test case #", "response ")) for issue in issues
        )
        return schema_valid, result.ok, issues, list(result.cases)

    if artifact_type is ArtifactType.CHECKLIST:
        result = validate_markdown(raw, items_under_every_section=True)
    elif artifact_type is ArtifactType.TESTPLAN:
        result = validate_markdown(raw, required_sections=TESTPLAN_SECTIONS)
    elif artifact_type is ArtifactType.QAREPORT:
        result = validate_markdown(raw, required_sections=QAREPORT_SECTIONS)
    elif artifact_type is ArtifactType.COVERAGEMATRIX:
        result = validate_markdown(
            raw,
            required_sections=COVERAGE_MATRIX_SECTIONS,
            require_table=True,
        )
    else:
        result = validate_markdown(raw)
    return False, result.ok, list(result.issues), []


def _extract_br_ids(text: str, prefix: str) -> list[str]:
    pattern = re.compile(rf"\b{re.escape(prefix)}-\d+\b")
    return list(dict.fromkeys(pattern.findall(text)))


def _duplicates(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _section_report(text: str, required: Sequence[str]) -> dict[str, list[str]]:
    lines = text.splitlines()
    headings: list[tuple[int, str, int]] = []
    for index, line in enumerate(lines):
        match = HEADING_PATTERN.match(line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip(), index))

    present: list[str] = []
    missing: list[str] = []
    empty: list[str] = []
    heading_titles: list[str] = []

    for position, (level, title, line_index) in enumerate(headings):
        heading_titles.append(title)
        body: list[str] = []
        for later in headings[position + 1 :]:
            later_level, _later_title, later_index = later
            if later_level <= level:
                body = lines[line_index + 1 : later_index]
                break
        else:
            body = lines[line_index + 1 :]
        meaningful = [line for line in body if line.strip() and not LIST_MARKER_PATTERN.match(line)]
        if not meaningful:
            empty.append(title)
        for wanted in required:
            if _heading_matches(title, wanted):
                present.append(wanted)
                break

    for wanted in required:
        if wanted not in present:
            missing.append(wanted)

    return {
        "headings": heading_titles,
        "present": list(dict.fromkeys(present)),
        "missing": missing,
        "empty": empty,
    }


def _heading_matches(title: str, wanted: str) -> bool:
    normalized = re.sub(r"^\d+[.)]\s*", "", title).casefold()
    needle = wanted.casefold()
    return normalized == needle or needle in normalized


def _claim_markers(
    text: str,
    artifact_type: ArtifactType,
    source_text: str,
) -> list[str]:
    if artifact_type not in {
        ArtifactType.QAREPORT,
        ArtifactType.TESTPLAN,
        ArtifactType.COVERAGEMATRIX,
    }:
        return []
    if _RESULT_SECTION.search(source_text):
        return []
    lowered = text.casefold()
    return [marker for marker in CLAIM_MARKERS if marker.casefold() in lowered]


def _is_truncated(text: str, json_valid: bool) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if not json_valid and stripped.endswith(("...", "{", "[", ",")):
        return True
    if stripped.startswith("```") and not stripped.endswith("```"):
        return True
    if len(stripped) > 200:
        last_line = stripped.splitlines()[-1].strip()
        if last_line and last_line[-1] not in _TERMINAL:
            return True
    return False


def title_tokens(text: str) -> set[str]:
    """Token set of the first ``#`` heading, used for canary/title Jaccard."""
    for line in text.splitlines():
        match = H1_PATTERN.match(line)
        if match:
            return token_set(match.group(1))
    return set()


def token_set(text: str) -> set[str]:
    normalized = re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE)
    return {token for token in normalized.split() if token}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def pairwise_mean_min(values: Sequence[set[str]]) -> tuple[float, float]:
    if len(values) < 2:
        return (1.0, 1.0)
    scores = [
        jaccard(values[i], values[j]) for i in range(len(values)) for j in range(i + 1, len(values))
    ]
    return (round(sum(scores) / len(scores), 4), round(min(scores), 4))
