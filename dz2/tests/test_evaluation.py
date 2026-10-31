import hashlib
import json
from pathlib import Path

import pytest

from ai_testgen.evaluation import (
    CRITERIA,
    CRITERIA_LABELS,
    JUDGE_ATTEMPTS,
    EvaluationError,
    EvaluationResult,
    artifact_reference,
    build_evaluator_prompt,
    evaluate_prompt,
    parse_criterion,
    quality_filename,
    quality_record,
)
from ai_testgen.models import ArtifactType

PROMPT = "Target site: https://x.com\nGenerate exactly 10 test cases with BR traceability."


class FakeJudge:
    """Returns one answer per call and records what each call received."""

    def __init__(
        self,
        scores: dict[str, int] | None = None,
        evidence: list[str] | None = None,
    ) -> None:
        self.scores = scores or {criterion: 4 for criterion in CRITERIA}
        self.evidence = evidence or ["Generate exactly 10 test cases"]
        self.calls: list[tuple[str, str, float, bool]] = []

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = True,
    ) -> str:
        self.calls.append((system, user, temperature, json_mode))
        criterion = next(item for item in CRITERIA if f"({item})" in system)
        return json.dumps(
            {
                "evidence": self.evidence,
                "score": self.scores[criterion],
                "comment": f"reasoning for {criterion}",
            }
        )


def test_labels_cover_every_criterion() -> None:
    assert tuple(CRITERIA_LABELS) == CRITERIA
    assert set(CRITERIA) == set(CRITERIA_LABELS)


def test_build_evaluator_prompt_targets_one_criterion() -> None:
    system, user = build_evaluator_prompt(
        PROMPT,
        "coverage",
        artifact_type=ArtifactType.TESTCASES,
        presets=["boundary-values"],
    )

    assert "Criterion under judgement: Покрытие сценариев (coverage)" in system
    assert "Structure and wording" not in system
    assert "Generate exactly 10 test cases" in user
    assert "testcases" in user
    assert "boundary-values" in user
    assert '"evidence"' in user


def test_build_evaluator_prompt_has_no_sample_numbers() -> None:
    """A filled-in example made the local judge copy 3/4/4/3 for every prompt."""

    system, user = build_evaluator_prompt(
        PROMPT, "readability", artifact_type=ArtifactType.CHECKLIST
    )

    for text in (system, user):
        assert '"scores"' not in text
        assert not any(f'"{criterion}": ' in text for criterion in CRITERIA)


def test_build_evaluator_prompt_forbids_absent_instead_of_quotes() -> None:
    system, _ = build_evaluator_prompt(
        PROMPT, "readability", artifact_type=ArtifactType.CHECKLIST
    )

    assert "Never write the word \"absent\"" in system
    assert "character for character" in system


def test_context_rubric_counts_a_link_inside_requirements() -> None:
    """The judge called the URL missing while quoting it as evidence."""

    system, _ = build_evaluator_prompt(
        PROMPT, "context", artifact_type=ArtifactType.COVERAGEMATRIX
    )

    assert "any http(s) link appears anywhere" in system
    assert "inside the requirements block" in system


def test_build_evaluator_prompt_rejects_empty() -> None:
    with pytest.raises(EvaluationError, match="empty"):
        build_evaluator_prompt(
            "   ", "readability", artifact_type=ArtifactType.TESTCASES
        )


def test_build_evaluator_prompt_rejects_unknown_criterion() -> None:
    with pytest.raises(EvaluationError, match="unknown criterion"):
        build_evaluator_prompt(
            "Prompt body", "speed", artifact_type=ArtifactType.TESTCASES
        )


def test_parse_criterion_accepts_verified_quote() -> None:
    raw = json.dumps(
        {
            "evidence": ["Generate exactly 10 test cases", "BR traceability"],
            "score": 4,
            "comment": "count and traceability are explicit",
        }
    )

    score, comment, quotes = parse_criterion(raw, "goal", prompt=PROMPT)

    assert score == 4
    assert comment == "count and traceability are explicit"
    assert quotes == ["Generate exactly 10 test cases", "BR traceability"]


def test_parse_criterion_accepts_quote_wrapped_in_stray_quotes() -> None:
    """The local judge wraps fragments like "'Generate exactly 10 test cases'"."""

    raw = json.dumps(
        {
            "evidence": ["'Generate exactly 10 test cases'", "«BR traceability»"],
            "score": 4,
            "comment": "explicit count",
        }
    )

    _, _, quotes = parse_criterion(raw, "goal", prompt=PROMPT)

    assert quotes == ["Generate exactly 10 test cases", "BR traceability"]


def test_parse_criterion_accepts_case_and_punctuation_differences() -> None:
    raw = json.dumps(
        {
            "evidence": ["generate exactly 10 TEST cases", "BR-traceability"],
            "score": 4,
            "comment": "same words",
        }
    )

    _, _, quotes = parse_criterion(raw, "goal", prompt=PROMPT)

    assert len(quotes) == 2


def test_parse_criterion_rejects_single_invented_word() -> None:
    """Token fallback needs at least two words; one stray word is not evidence."""

    raw = json.dumps(
        {"evidence": ["checklist"], "score": 2, "comment": "weak"}
    )

    with pytest.raises(EvaluationError, match="quoted nothing"):
        parse_criterion(raw, "coverage", prompt=PROMPT)


def test_parse_criterion_drops_quotes_that_are_not_in_the_prompt() -> None:
    raw = json.dumps(
        {
            "evidence": ["this fragment is invented", "Generate exactly 10"],
            "score": 3,
            "comment": "mixed",
        }
    )

    _, _, quotes = parse_criterion(raw, "goal", prompt=PROMPT)

    assert quotes == ["Generate exactly 10"]


def test_parse_criterion_rejects_answer_without_any_real_quote() -> None:
    raw = json.dumps(
        {
            "evidence": ["absent", "not in the prompt at all"],
            "score": 1,
            "comment": "nothing found",
        }
    )

    with pytest.raises(EvaluationError, match="quoted nothing"):
        parse_criterion(raw, "readability", prompt=PROMPT)


def test_parse_criterion_allows_score_one_without_quotes_when_asked() -> None:
    raw = json.dumps(
        {"evidence": [], "score": 1, "comment": "no scenarios at all"}
    )

    score, comment, quotes = parse_criterion(
        raw, "coverage", prompt=PROMPT, require_evidence=False
    )

    assert score == 1
    assert comment == "no scenarios at all"
    assert quotes == []


def test_parse_criterion_strips_code_fences() -> None:
    raw = (
        "```json\n"
        + json.dumps(
            {"evidence": ["BR traceability"], "score": 5, "comment": "ok"}
        )
        + "\n```"
    )

    score, _, quotes = parse_criterion(raw, "coverage", prompt=PROMPT)

    assert score == 5
    assert quotes == ["BR traceability"]


@pytest.mark.parametrize(
    "payload, message",
    [
        ("not json", "not valid JSON"),
        ('["score"]', "JSON object"),
        ('{"comment": "ok", "score": "4", "evidence": []}', "integer"),
        ('{"comment": "ok", "score": true, "evidence": []}', "integer"),
        ('{"comment": "ok", "score": 6, "evidence": []}', "out of range"),
        ('{"comment": "ok", "score": 0, "evidence": []}', "out of range"),
        ('{"comment": "  ", "score": 4, "evidence": []}', "no comment"),
        ('{"comment": "ok", "score": 4}', "evidence"),
    ],
)
def test_parse_criterion_rejects_invalid_payloads(payload: str, message: str) -> None:
    with pytest.raises(EvaluationError, match=message):
        parse_criterion(payload, "readability", prompt=PROMPT)


def test_evaluate_prompt_asks_once_per_criterion_at_zero_temperature() -> None:
    judge = FakeJudge({"readability": 5, "context": 4, "goal": 4, "coverage": 2})

    result = evaluate_prompt(
        judge,  # type: ignore[arg-type]
        PROMPT,
        artifact_type=ArtifactType.TESTCASES,
    )

    assert result.scores == {
        "readability": 5,
        "context": 4,
        "goal": 4,
        "coverage": 2,
    }
    assert result.average == 3.75
    assert result.evidence["coverage"] == ["Generate exactly 10 test cases"]
    assert len(judge.calls) == len(CRITERIA)
    assert all(call[2] == 0.0 for call in judge.calls)
    assert all(call[3] is True for call in judge.calls)
    assert all("Generate exactly 10" in call[1] for call in judge.calls)
    # Each criterion is graded by its own rubric, never by one shared bundle.
    systems = [call[0] for call in judge.calls]
    assert len(set(systems)) == len(CRITERIA)


def test_evaluate_prompt_reports_progress_per_criterion() -> None:
    seen: list[tuple[str, int]] = []

    evaluate_prompt(
        FakeJudge(),  # type: ignore[arg-type]
        PROMPT,
        artifact_type=ArtifactType.TESTPLAN,
        on_criterion=lambda criterion, score: seen.append((criterion, score)),
    )

    assert [criterion for criterion, _ in seen] == list(CRITERIA)


class FirstAnswerBadJudge(FakeJudge):
    """Fails the very first criterion once, then answers normally."""

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = True,
    ) -> str:
        self.calls.append((system, user, temperature, json_mode))
        if len(self.calls) == 1:
            return json.dumps(
                {
                    "evidence": ["fragment retyped from memory"],
                    "score": 3,
                    "comment": "quote is not in the prompt",
                }
            )
        criterion = next(item for item in CRITERIA if f"({item})" in system)
        return json.dumps(
            {
                "evidence": self.evidence,
                "score": self.scores[criterion],
                "comment": f"reasoning for {criterion}",
            }
        )


def test_evaluate_prompt_retries_once_when_judge_cannot_quote() -> None:
    judge = FirstAnswerBadJudge(
        {"readability": 5, "context": 4, "goal": 4, "coverage": 2}
    )

    result = evaluate_prompt(
        judge,  # type: ignore[arg-type]
        PROMPT,
        artifact_type=ArtifactType.TESTCASES,
    )

    assert result.scores["readability"] == 5
    assert len(judge.calls) == len(CRITERIA) + 1
    assert "previous answer was rejected" in judge.calls[1][1]
    assert result.evidence["readability"] == ["Generate exactly 10 test cases"]


def test_evaluate_prompt_gives_up_after_retry_budget() -> None:
    judge = FakeJudge(evidence=["fragment retyped from memory"])

    with pytest.raises(EvaluationError, match=f"after {JUDGE_ATTEMPTS} attempts"):
        evaluate_prompt(
            judge,  # type: ignore[arg-type]
            PROMPT,
            artifact_type=ArtifactType.TESTCASES,
        )

    assert len(judge.calls) == JUDGE_ATTEMPTS


class LowScoreJudge(FakeJudge):
    """Scores coverage 1 and honestly has nothing to quote for it."""

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = True,
    ) -> str:
        self.calls.append((system, user, temperature, json_mode))
        criterion = next(item for item in CRITERIA if f"({item})" in system)
        if criterion == "coverage":
            return json.dumps(
                {
                    "evidence": [],
                    "score": 1,
                    "comment": "no scenario groups are requested",
                }
            )
        return json.dumps(
            {
                "evidence": self.evidence,
                "score": self.scores[criterion],
                "comment": f"reasoning for {criterion}",
            }
        )


def test_evaluate_prompt_accepts_unquoted_score_one_without_retry() -> None:
    judge = LowScoreJudge()

    result = evaluate_prompt(
        judge,  # type: ignore[arg-type]
        PROMPT,
        artifact_type=ArtifactType.QAREPORT,
    )

    assert result.scores["coverage"] == 1
    assert result.evidence["coverage"] == []
    assert len(judge.calls) == len(CRITERIA)


def test_quality_filename_adds_sorted_presets_and_run_id() -> None:
    assert quality_filename(ArtifactType.TESTCASES) == "testcases.json"
    assert (
        quality_filename(
            ArtifactType.QAREPORT,
            ["negative", "accessibility"],
            "20261031T091703000000Z",
        )
        == "qareport-accessibility-negative-20261031T091703000000Z.json"
    )


def test_quality_record_is_reproducible() -> None:
    result = EvaluationResult(
        scores={"readability": 4, "context": 5, "goal": 4, "coverage": 3},
        comments={criterion: "" for criterion in CRITERIA},
        summary="ok",
        evidence={"readability": ["Generate exactly 10 test cases"]},
    )

    payload = quality_record(
        artifact_type=ArtifactType.TESTCASES,
        model="qwen2.5:7b-instruct",
        prompt=PROMPT,
        result=result,
        presets=["boundary-values"],
        count=10,
        created_at="2026-10-29T00:00:00+00:00",
    )

    assert payload["artifact"] == "testcases"
    assert payload["run_id"] == "20261029T000000000000Z"
    assert payload["source"] == "llm"
    assert payload["presets"] == ["boundary-values"]
    assert payload["judge_temperature"] == 0.0
    assert payload["judge_version"] == 4
    assert payload["generated_artifact"] is None
    assert payload["evidence"]["readability"] == ["Generate exactly 10 test cases"]
    assert payload["prompt_sha256"] == hashlib.sha256(PROMPT.encode()).hexdigest()
    assert payload["average"] == 4.0
    assert payload["created_at"] == "2026-10-29T00:00:00+00:00"


def test_artifact_reference_is_relative_and_hashed(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    artifact = output_dir / "requirements.testcases.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# cases\n", encoding="utf-8")

    reference = artifact_reference(artifact, output_dir)

    assert reference is not None
    assert reference["path"] == "requirements.testcases.md"
    assert reference["sha256"] == hashlib.sha256(b"# cases\n").hexdigest()
    assert reference["bytes"] == 8
    assert reference["modified_at"]

    assert artifact_reference(output_dir / "missing.md", output_dir) is None
