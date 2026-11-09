"""Golden inputs, probes and recorded responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from ai_testgen.prompt import QAREPORT_SECTIONS
from ai_testgen.validator import validate, validate_markdown

from llm_degradation.config import Thresholds
from llm_degradation.models import NormalizedInput
from llm_degradation.normalizer_bridge import load_normalized
from llm_degradation.probes.canary import compare_canary_results, parse_canonical
from llm_degradation.probes.hallucinations import load_probes, load_refusal_markers
from llm_degradation.report import load_report


def recorded(golden_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((golden_dir / "recorded" / name / "run-01.json").read_text(encoding="utf-8"))


def test_catalog_input_is_cp1251_with_seven_requirements(
    golden_dir: Path, normalizer_config: Path
) -> None:
    normalized = load_normalized(
        golden_dir / "inputs" / "catalog.md", normalizer_config=normalizer_config
    )
    assert normalized.encoding.lower().replace("-", "") == "cp1251"
    assert normalized.br_ids == tuple(f"BR-{index}" for index in range(1, 8))
    assert normalized.url == "https://otus.ru/catalog/courses?categories=programming"


def test_no_cart_input_has_no_commerce_entities(
    normalized: NormalizedInput,
) -> None:
    lowered = normalized.text.casefold()
    for term in ("корзин", "оплат", "промокод", "личный кабинет"):
        assert term not in lowered
    assert normalized.br_ids == ("BR-1", "BR-2", "BR-3", "BR-4")


def test_recorded_testcases_validate(golden_dir: Path) -> None:
    payload = recorded(golden_dir, "testcases")
    content = payload["response"]["message"]["content"]
    result = validate(content, 5)
    assert result.ok is True
    assert len(result.cases) == 5
    assert payload["request"]["model"] == "llama3"
    assert payload["request"]["options"]["seed"] == 42


def test_recorded_qareport_validates(golden_dir: Path) -> None:
    payload = recorded(golden_dir, "qareport")
    content = payload["response"]["message"]["content"]
    result = validate_markdown(content, required_sections=QAREPORT_SECTIONS)
    assert result.ok is True


def test_recorded_canary_matches_the_normalizer(
    golden_dir: Path, normalized: NormalizedInput
) -> None:
    payload = recorded(golden_dir, "canary")
    content = payload["response"]["message"]["content"]
    parsed = parse_canonical(content)
    assert parsed.parse_error is None
    assert parsed.ids == ["BR-1", "BR-2", "BR-3", "BR-4"]

    compared = compare_canary_results(content, normalized.text, thresholds=Thresholds())
    assert compared.verdict in {"pass", "warn"}
    assert compared.ids_exact is True
    assert compared.url_exact is True


def test_probe_file_is_complete(golden_dir: Path) -> None:
    probes = load_probes(golden_dir / "probes" / "hallucinations.json")
    assert len(probes) == 8
    assert len({item.id for item in probes}) == 8
    assert {item.id for item in probes if item.critical} == {"H-04", "H-06", "H-07"}
    for probe in probes:
        assert Path(probe.input)
        assert (golden_dir.parent / probe.input).is_file() or Path(probe.input).is_file()


def test_probe_count_matches_catalog(golden_dir: Path, normalizer_config: Path) -> None:
    probes = load_probes(golden_dir / "probes" / "hallucinations.json")
    count_probe = next(item for item in probes if item.id == "H-01")
    normalized = load_normalized(
        golden_dir.parent / count_probe.input, normalizer_config=normalizer_config
    )
    assert count_probe.expected["number"] == len(normalized.br_ids)


def test_refusal_markers_include_russian_and_english(golden_dir: Path) -> None:
    markers = load_refusal_markers(golden_dir / "probes" / "refusal_markers.txt")
    assert "нет данных" in markers
    assert any("not" in marker or "no " in marker for marker in markers)


def test_golden_baseline_reports_are_valid(golden_dir: Path, repo_root: Path) -> None:
    baseline_dir = golden_dir / "baseline"
    if not baseline_dir.is_dir() or not any(baseline_dir.glob("*.json")):
        pytest.skip("laboratory baseline has not been recorded yet")
    for path in sorted(baseline_dir.glob("*.json")):
        report = load_report(path)
        assert report.prompt.prompt_sha256
        assert report.kind == "consistency"
        assert report.summary


def test_recorded_runs_have_request_and_response(golden_dir: Path) -> None:
    for name in ("testcases", "qareport", "canary"):
        payload = recorded(golden_dir, name)
        assert payload["request"]["messages"]
        assert payload["response"]["message"]["content"]
