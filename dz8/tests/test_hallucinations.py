"""Hallucination probes: deterministic text checks and run aggregation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_degradation.config import Thresholds
from llm_degradation.models import NormalizedInput
from llm_degradation.ollama import ChatResponse
from llm_degradation.probes.hallucinations import (
    ProbeDefinition,
    _evaluate_text_probe,
    load_probes,
    load_refusal_markers,
    run_hallucinations,
)
from llm_degradation.runner import SeriesOptions

MARKERS = ["данных нет", "не указан", "not specified"]


def probe(kind: str, **kwargs: Any) -> ProbeDefinition:
    data: dict[str, Any] = {"id": "P-01", "kind": kind, "input": "doc.md"}
    data.update(kwargs)
    return ProbeDefinition.model_validate(data)


def evaluate(
    definition: ProbeDefinition,
    answer: str,
    normalized: NormalizedInput,
    markers: list[str] | None = None,
) -> tuple[bool, list[str], list[str]]:
    return _evaluate_text_probe(definition, answer, normalized, markers or MARKERS)


def test_load_probes_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "probes.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "probes": [
                    {
                        "id": "H-01",
                        "kind": "count",
                        "input": "doc.md",
                        "critical": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    probes = load_probes(path)
    assert probes[0].id == "H-01"
    assert probes[0].critical is True


def test_load_probes_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        load_probes(tmp_path / "missing.json")


def test_load_probes_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "probes.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_probes(path)


def test_load_probes_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "probes.json"
    path.write_text(
        json.dumps({"schema_version": 2, "probes": [{"id": "x", "kind": "count", "input": "d"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema_version"):
        load_probes(path)


def test_load_probes_rejects_empty_set(tmp_path: Path) -> None:
    path = tmp_path / "probes.json"
    path.write_text(json.dumps({"schema_version": 1, "probes": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="no probes"):
        load_probes(path)


def test_load_refusal_markers_normalizes(tmp_path: Path) -> None:
    path = tmp_path / "markers.txt"
    path.write_text("Not Specified\n\n  данных нет  \n", encoding="utf-8")
    assert load_refusal_markers(path) == ["not specified", "данных нет"]


def test_load_refusal_markers_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "markers.txt"
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_refusal_markers(path)


def test_count_probe_passes_on_expected_number(normalized: NormalizedInput) -> None:
    passed, forbidden, details = evaluate(
        probe("count", expected={"number": 4}), "В документе 4 требования.", normalized
    )
    assert passed is True
    assert forbidden == []
    assert details == []


def test_count_probe_fails_on_wrong_number(normalized: NormalizedInput) -> None:
    passed, _, details = evaluate(
        probe("count", expected={"number": 4}), "В документе 5 требований.", normalized
    )
    assert passed is False
    assert "expected number 4" in details[0]


def test_missing_url_probe_passes_without_urls(normalized: NormalizedInput) -> None:
    passed, _, _ = evaluate(probe("missing_url"), "URL в документе не указано.", normalized)
    assert passed is True


def test_missing_url_probe_flags_invented_url(normalized: NormalizedInput) -> None:
    passed, forbidden, details = evaluate(
        probe("missing_url"), "См. https://invented.example/page", normalized
    )
    assert passed is False
    assert forbidden == ["https://invented.example/page"]
    assert "invented urls" in details[0]


def test_missing_url_probe_accepts_known_url(normalized: NormalizedInput) -> None:
    passed, _, _ = evaluate(
        probe("missing_url"),
        f"См. {normalized.url}",
        normalized,
    )
    assert passed is True


def test_numeric_probe_requires_refusal(normalized: NormalizedInput) -> None:
    passed, _, details = evaluate(probe("numeric"), "Цена отсутствует.", normalized)
    assert passed is False
    assert "no refusal marker in the answer" in details


def test_numeric_probe_flags_currency(normalized: NormalizedInput) -> None:
    passed, forbidden, _ = evaluate(
        probe("numeric"), "Стоимость 1000 ₽, но данных нет.", normalized
    )
    assert passed is False
    assert forbidden


def test_missing_feature_probe_detects_forbidden_term(
    normalized: NormalizedInput,
) -> None:
    definition = probe("missing_feature", forbidden_terms=["корзина"])
    passed, forbidden, _ = evaluate(definition, "В документе есть корзина.", normalized)
    assert passed is False
    assert forbidden == ["корзина"]


def test_missing_feature_probe_accepts_refusal(normalized: NormalizedInput) -> None:
    definition = probe("missing_feature", forbidden_terms=["корзина"])
    passed, _, _ = evaluate(definition, "Данных нет: функционал не описан.", normalized)
    assert passed is True


def test_negated_term_is_allowed_when_requested(
    normalized: NormalizedInput,
) -> None:
    definition = probe("entity", forbidden_terms=["промокод"], allow_negated=True)
    passed, _, _ = evaluate(definition, "Данных нет: промокод отсутствует.", normalized)
    assert passed is True


def test_empty_response_fails(normalized: NormalizedInput) -> None:
    passed, _, details = evaluate(probe("entity"), "   ", normalized)
    assert passed is False
    assert details == ["empty response"]


def test_generic_kind_flags_forbidden_terms(normalized: NormalizedInput) -> None:
    definition = probe("other", forbidden_terms=["оплата"])
    passed, forbidden, _ = evaluate(definition, "Оплата картой доступна.", normalized)
    assert passed is False
    assert forbidden == ["оплата"]


class FakeProbeClient:
    def __init__(self, text_answers: list[str], artifact_answers: list[str] | None = None) -> None:
        self.text_answers = list(text_answers)
        self.artifact_answers = list(artifact_answers or [])
        self.text_calls = 0
        self.artifact_calls = 0

    def chat_messages(self, messages: list[dict[str, str]], **_: Any) -> ChatResponse:
        self.text_calls += 1
        if not self.text_answers:
            raise AssertionError("unexpected text call")
        return ChatResponse(model="llama3", content=self.text_answers.pop(0), done=True)

    def chat(self, system: str, user: str, temperature: float, **_: Any) -> str:
        self.artifact_calls += 1
        if not self.artifact_answers:
            raise AssertionError("unexpected artifact call")
        return self.artifact_answers.pop(0)


def golden_with_markers(tmp_path: Path) -> Path:
    golden = tmp_path / "golden"
    (golden / "probes").mkdir(parents=True)
    (golden / "probes" / "refusal_markers.txt").write_text("\n".join(MARKERS), encoding="utf-8")
    return golden


def test_run_hallucinations_aggregates_runs(
    normalized: NormalizedInput,
    tmp_path: Path,
) -> None:
    golden = golden_with_markers(tmp_path)
    probes = [
        probe("count", id="H-01", input=str(normalized.source), expected={"number": 4}),
        probe(
            "count",
            id="H-02",
            input=str(normalized.source),
            expected={"number": 4},
            critical=True,
        ),
    ]
    client = FakeProbeClient(["4", "7"])

    result = run_hallucinations(
        client,  # type: ignore[arg-type]
        probes,
        model="llama3",
        root=tmp_path,
        golden_dir=golden,
        options=SeriesOptions(runs=1),
        runs=1,
        strict=False,
        thresholds=Thresholds(),
        normalizer_config=None,
    )

    assert result.summary["probes_total"] == 2
    assert result.summary["failed_probes"] == 1
    assert result.summary["hallucination_rate"] == 0.5
    assert result.summary["critical_failures"] == ["H-02"]
    assert result.verdict.status == "fail"
    assert "critical probes failed" in result.verdict.reasons[0]


def test_run_hallucinations_passes_on_clean_answers(
    normalized: NormalizedInput,
    tmp_path: Path,
) -> None:
    golden = golden_with_markers(tmp_path)
    probes = [probe("count", id="H-01", input=str(normalized.source), expected={"number": 4})]
    client = FakeProbeClient(["4"])

    result = run_hallucinations(
        client,  # type: ignore[arg-type]
        probes,
        model="llama3",
        root=tmp_path,
        golden_dir=golden,
        options=SeriesOptions(runs=1),
        runs=1,
        strict=False,
        thresholds=Thresholds(),
        normalizer_config=None,
    )

    assert result.verdict.status == "pass"
    assert result.summary["failed_probes"] == 0
    assert result.summary["runs_total"] == 1


def test_strict_mode_makes_every_probe_critical(
    normalized: NormalizedInput,
    tmp_path: Path,
) -> None:
    golden = golden_with_markers(tmp_path)
    probes = [probe("count", id="H-01", input=str(normalized.source), expected={"number": 4})]
    client = FakeProbeClient(["5"])

    result = run_hallucinations(
        client,  # type: ignore[arg-type]
        probes,
        model="llama3",
        root=tmp_path,
        golden_dir=golden,
        options=SeriesOptions(runs=1),
        runs=1,
        strict=True,
        thresholds=Thresholds(hallucination_rate_max=1.0),
        normalizer_config=None,
    )

    assert result.verdict.status == "fail"
    assert result.probes[0].critical is True


def test_artifact_probe_uses_max_retries_zero(
    normalized: NormalizedInput,
    tmp_path: Path,
    prompt_dir: Path,
) -> None:
    golden = golden_with_markers(tmp_path)
    payload = {
        "cases": [
            {
                "id": f"TC-0{index + 1}",
                "type": "negative" if index % 2 else "positive",
                "title": "Sample",
                "steps": ["step"],
                "expected": "result",
                "requirement": "BR-99",
            }
            for index in range(5)
        ]
    }
    probes = [
        probe(
            "artifact",
            id="H-03",
            input=str(normalized.source),
            artifact_type="testcases",
            count=5,
            critical=True,
            checks=["no_invented_br"],
        )
    ]
    client = FakeProbeClient([], [json.dumps(payload)])

    result = run_hallucinations(
        client,  # type: ignore[arg-type]
        probes,
        model="llama3",
        root=tmp_path,
        golden_dir=golden,
        options=SeriesOptions(runs=1, max_retries=3),
        runs=1,
        strict=False,
        thresholds=Thresholds(),
        normalizer_config=None,
        prompt_dir=prompt_dir,
    )

    assert client.artifact_calls == 1
    assert result.probes[0].status == "fail"
    assert "BR-99" in result.probes[0].forbidden_hits
    assert "invented requirement ids" in result.probes[0].details[0]


def test_artifact_probe_passes_with_known_br(
    normalized: NormalizedInput,
    tmp_path: Path,
    prompt_dir: Path,
) -> None:
    golden = golden_with_markers(tmp_path)
    payload = {
        "cases": [
            {
                "id": f"TC-0{index + 1}",
                "type": "negative" if index % 2 else "positive",
                "title": "Sample",
                "steps": ["step"],
                "expected": "result",
                "requirement": f"BR-{min(index + 1, 4)}",
            }
            for index in range(5)
        ]
    }
    probes = [
        probe(
            "artifact",
            id="H-04",
            input=str(normalized.source),
            artifact_type="testcases",
            count=5,
            checks=["no_invented_br", "validator_pass"],
        )
    ]
    client = FakeProbeClient([], [json.dumps(payload)])

    result = run_hallucinations(
        client,  # type: ignore[arg-type]
        probes,
        model="llama3",
        root=tmp_path,
        golden_dir=golden,
        options=SeriesOptions(runs=1),
        runs=1,
        strict=False,
        thresholds=Thresholds(),
        normalizer_config=None,
        prompt_dir=prompt_dir,
    )

    assert result.probes[0].status == "pass"
    assert result.verdict.status == "pass"
