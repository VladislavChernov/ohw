"""Deterministic artifact metrics."""

from __future__ import annotations

import json
from typing import Any

import pytest
from ai_testgen.models import ArtifactType

from llm_degradation.metrics import (
    artifact_metrics,
    jaccard,
    normalize_url,
    pairwise_mean_min,
    required_sections_for,
    title_tokens,
    token_set,
)

SOURCE_BRS = ["BR-1", "BR-2", "BR-3", "BR-4", "BR-5"]
SOURCE_URLS = ["https://otus.ru/catalog/courses"]


def case(case_id: str, kind: str = "positive", requirement: str = "BR-1") -> dict[str, Any]:
    return {
        "id": case_id,
        "type": kind,
        "title": "Sample title",
        "preconditions": ["user is on the catalog page"],
        "steps": ["open the page"],
        "expected": "the page is shown",
        "requirement": requirement,
    }


def valid_payload(count: int = 5) -> str:
    kinds = ["positive", "negative", "positive", "negative", "negative"]
    cases = [
        case(f"TC-{index + 1:02d}", kinds[index % len(kinds)], f"BR-{index + 1}")
        for index in range(count)
    ]
    return json.dumps({"cases": cases}, ensure_ascii=False)


def test_valid_testcases_artifact() -> None:
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        valid_payload(),
        expected_count=5,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert metrics.json_valid is True
    assert metrics.schema_valid is True
    assert metrics.validator_pass is True
    assert metrics.cases_total == 5
    assert metrics.positive == 2
    assert metrics.negative == 3
    assert metrics.negative_ratio == 0.6
    assert metrics.count_matches is True
    assert metrics.br_covered == SOURCE_BRS
    assert metrics.br_missing == []
    assert metrics.br_invented == []
    assert metrics.br_coverage == 1.0
    assert metrics.tc_ids == ["TC-01", "TC-02", "TC-03", "TC-04", "TC-05"]
    assert metrics.tc_duplicates == []


def test_broken_json_artifact() -> None:
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        '{"cases": [{"id": "TC-01"',
        expected_count=5,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert metrics.json_valid is False
    assert metrics.schema_valid is False
    assert metrics.validator_pass is False
    assert metrics.cases_total == 0
    assert metrics.validator_issues


def test_empty_response() -> None:
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        "",
        expected_count=5,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert metrics.raw_chars == 0
    assert metrics.raw_lines == 0
    assert metrics.token_count == 0
    assert metrics.tc_ids == []
    assert metrics.truncated is False
    assert metrics.br_coverage == 0.0


def test_empty_response_without_source_requirements() -> None:
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        "",
        expected_count=1,
        source_br_ids=[],
        source_urls=[],
    )
    assert metrics.br_coverage == 1.0


def test_large_response_is_measured() -> None:
    raw = "word " * 5000
    metrics = artifact_metrics(
        ArtifactType.TESTPLAN,
        raw,
        expected_count=1,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert metrics.raw_chars == len(raw)
    assert metrics.token_count == 5000
    assert metrics.raw_lines == 1


def test_br_and_tc_and_url_extraction() -> None:
    raw = valid_payload() + "\nSee BR-1, BR-9 and https://invented.example/page."
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        raw,
        expected_count=5,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert "BR-9" in metrics.br_ids_output
    assert metrics.br_invented == ["BR-9"]
    assert metrics.br_covered == SOURCE_BRS
    assert metrics.urls_invented == ["https://invented.example/page"]


def test_duplicate_tc_ids_are_reported() -> None:
    payload = json.loads(valid_payload())
    payload["cases"][1]["id"] = "TC-01"
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        json.dumps(payload),
        expected_count=5,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert metrics.tc_duplicates == ["TC-01"]


def test_code_fence_is_detected() -> None:
    fenced = "```json\n" + valid_payload() + "\n```"
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        fenced,
        expected_count=5,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert metrics.has_code_fence is True


def test_truncated_by_ellipsis() -> None:
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        "Here is a partial answer...",
        expected_count=5,
        source_br_ids=[],
        source_urls=[],
    )
    assert metrics.truncated is True


def test_truncated_by_unclosed_fence() -> None:
    metrics = artifact_metrics(
        ArtifactType.TESTPLAN,
        "```markdown\n# Plan\n## Scope\nsome content",
        expected_count=1,
        source_br_ids=[],
        source_urls=[],
    )
    assert metrics.truncated is True


def test_complete_short_answer_is_not_truncated() -> None:
    metrics = artifact_metrics(
        ArtifactType.TESTPLAN,
        "## Scope\nDone.",
        expected_count=1,
        source_br_ids=[],
        source_urls=[],
    )
    assert metrics.truncated is False


def test_claims_are_flagged_for_qareport() -> None:
    raw = "# QA report\n\nAll tests passed, no defects found.\n"
    metrics = artifact_metrics(
        ArtifactType.QAREPORT,
        raw,
        expected_count=1,
        source_br_ids=[],
        source_urls=[],
    )
    assert metrics.claims_execution_results is True
    assert "all tests passed" in metrics.claims_markers
    assert "no defects found" in metrics.claims_markers


def test_claims_are_ignored_when_source_has_result_section() -> None:
    raw = "# QA report\n\nAll tests passed.\n"
    metrics = artifact_metrics(
        ArtifactType.QAREPORT,
        raw,
        expected_count=1,
        source_br_ids=[],
        source_urls=[],
        source_text="## Результаты прогона тестов\n",
    )
    assert metrics.claims_execution_results is False


def test_testcases_never_report_execution_claims() -> None:
    raw = valid_payload() + "\nAll tests passed."
    metrics = artifact_metrics(
        ArtifactType.TESTCASES,
        raw,
        expected_count=5,
        source_br_ids=SOURCE_BRS,
        source_urls=SOURCE_URLS,
    )
    assert metrics.claims_execution_results is False


def test_required_sections_for_known_types() -> None:
    assert required_sections_for(ArtifactType.TESTPLAN)
    assert required_sections_for(ArtifactType.QAREPORT)
    assert required_sections_for(ArtifactType.COVERAGEMATRIX)
    assert required_sections_for(ArtifactType.TESTCASES) == ()


def test_missing_and_present_required_sections() -> None:
    raw = "## Scope\nContent.\n\n## Risks\n\n"
    metrics = artifact_metrics(
        ArtifactType.TESTPLAN,
        raw,
        expected_count=1,
        source_br_ids=[],
        source_urls=[],
        required_sections=["Scope", "Risks", "Approach"],
    )
    assert "Scope" in metrics.required_sections_present
    assert "Risks" in metrics.required_sections_present
    assert "Approach" in metrics.required_sections_missing
    assert "Risks" in metrics.empty_sections


def test_title_tokens_uses_first_h1() -> None:
    raw = "# Catalog regression suite\n\n## Scope\n"
    assert title_tokens(raw) == {"catalog", "regression", "suite"}


def test_token_set_normalizes_case_and_punctuation() -> None:
    assert token_set("Catalog, COURSES!") == {"catalog", "courses"}


def test_jaccard_edge_cases() -> None:
    assert jaccard(set(), set()) == 1.0
    assert jaccard({"a"}, {"a"}) == 1.0
    assert jaccard({"a"}, {"b"}) == 0.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_pairwise_mean_and_min() -> None:
    mean, minimum = pairwise_mean_min([{"a"}, {"a"}, {"b"}])
    assert mean == pytest.approx(1 / 3, abs=1e-4)
    assert minimum == 0.0


def test_pairwise_requires_two_sets() -> None:
    assert pairwise_mean_min([]) == (1.0, 1.0)
    assert pairwise_mean_min([{"a"}]) == (1.0, 1.0)


def test_normalize_url_trims_noise() -> None:
    assert normalize_url("https://example.test/a/") == "https://example.test/a"
    assert normalize_url("https://example.test/a#frag") == "https://example.test/a"
    assert normalize_url("https://example.test/a).") == "https://example.test/a"
