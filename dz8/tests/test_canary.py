"""Canary: parse canonical Markdown and compare it with the normalizer."""

from __future__ import annotations

from typing import Any

from llm_degradation.config import Thresholds
from llm_degradation.models import NormalizedInput
from llm_degradation.ollama import ChatResponse
from llm_degradation.probes.canary import (
    _worst,
    canary_prompt,
    canary_system,
    compare_canary_results,
    parse_canonical,
    run_canary,
)
from llm_degradation.runner import SeriesOptions


def canonical(
    *,
    title: str = "Catalog",
    url: str = "https://otus.ru/catalog/courses",
    bodies: dict[str, str] | None = None,
) -> str:
    bodies = bodies or {
        "BR-1": "Каталог курсов доступен пользователю.",
        "BR-2": "Корзина хранит выбранные курсы.",
    }
    lines = [f"# {title}", f"**URL:** {url}", "", "## Requirements", ""]
    for item, body in bodies.items():
        lines.extend([f"### {item}", body, ""])
    return "\n".join(lines).strip()


def test_canary_prompt_mentions_prefix_and_document(normalized: NormalizedInput) -> None:
    prompt = canary_prompt(normalized)
    assert normalized.settings.id_prefix in prompt
    assert normalized.text in prompt
    assert "=== ДОКУМЕНТ ===" in prompt


def test_parse_canonical_extracts_fields() -> None:
    parsed = parse_canonical(canonical())
    assert parsed.title == "Catalog"
    assert parsed.url == "https://otus.ru/catalog/courses"
    assert parsed.ids == ["BR-1", "BR-2"]
    assert parsed.bodies["BR-1"] == "Каталог курсов доступен пользователю."
    assert parsed.parse_error is None


def test_parse_canonical_without_structure_reports_error() -> None:
    parsed = parse_canonical("just plain text")
    assert parsed.parse_error is not None
    assert parsed.ids == []


def test_identical_documents_pass() -> None:
    result = compare_canary_results(canonical(), canonical(), thresholds=Thresholds())
    assert result.verdict == "pass"
    assert result.ids_exact is True
    assert result.count_exact is True
    assert result.url_exact is True
    assert result.title_overlap == 1.0
    assert result.body_overlap_mean == 1.0
    assert result.extra_ids == []
    assert result.missing_ids == []


def test_reordered_ids_fail() -> None:
    llm = canonical(
        bodies={
            "BR-2": "Корзина хранит выбранные курсы.",
            "BR-1": "Каталог курсов доступен пользователю.",
        }
    )
    result = compare_canary_results(llm, canonical(), thresholds=Thresholds())
    assert result.verdict == "fail"
    assert result.ids_exact is False
    assert result.count_exact is True


def test_extra_and_missing_ids_are_reported() -> None:
    llm = canonical(bodies={"BR-1": "Каталог курсов доступен пользователю.", "BR-9": "Лишнее."})
    result = compare_canary_results(llm, canonical(), thresholds=Thresholds())
    assert result.extra_ids == ["BR-9"]
    assert result.missing_ids == ["BR-2"]
    assert result.count_exact is True


def test_url_difference_fails() -> None:
    llm = canonical(url="https://invented.example/page")
    result = compare_canary_results(llm, canonical(), thresholds=Thresholds())
    assert result.url_exact is False
    assert result.verdict == "fail"


def test_low_title_overlap_fails() -> None:
    llm = canonical(title="Totally unrelated words")
    result = compare_canary_results(llm, canonical(), thresholds=Thresholds())
    assert result.title_overlap == 0.0
    assert result.verdict == "fail"


def test_partial_body_overlap_warns() -> None:
    llm = canonical(
        bodies={
            "BR-1": "Каталог курсов доступен пользователю всегда.",
            "BR-2": "Корзина хранит выбранные курсы.",
        }
    )
    result = compare_canary_results(llm, canonical(), thresholds=Thresholds())
    assert result.verdict == "warn"
    assert 0.5 <= result.body_overlap_min < 0.95


def test_too_low_body_overlap_fails() -> None:
    llm = canonical(bodies={"BR-1": "alpha beta", "BR-2": "gamma delta"})
    result = compare_canary_results(llm, canonical(), thresholds=Thresholds())
    assert result.verdict == "fail"
    assert result.body_overlap_min == 0.0


def test_parse_error_fails_without_comparison() -> None:
    result = compare_canary_results("garbage", canonical(), thresholds=Thresholds())
    assert result.verdict == "fail"
    assert result.parse_error is not None
    assert result.ids_exact is False


class FakeClient:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def chat_messages(self, messages: list[dict[str, str]], **kwargs: Any) -> ChatResponse:
        self.calls.append({"messages": messages, **kwargs})
        if not self.answers:
            raise AssertionError("unexpected chat call")
        return ChatResponse(model=kwargs.get("model", "llama3"), content=self.answers.pop(0))


def test_run_canary_reports_every_run(normalized: NormalizedInput) -> None:
    seen: list[int] = []
    client = FakeClient([normalized.text, normalized.text])

    run = run_canary(
        client,  # type: ignore[arg-type]
        normalized,
        model="llama3",
        runs=2,
        options=SeriesOptions(runs=2),
        thresholds=Thresholds(),
        on_run=lambda index, _total, _result: seen.append(index),
    )

    assert run.verdict == "pass"
    assert len(run.results) == 2
    assert seen == [1, 2]
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["messages"][0]["content"] == canary_system()


def test_run_canary_uses_the_worst_run(normalized: NormalizedInput) -> None:
    client = FakeClient([normalized.text, "not a canonical document"])
    run = run_canary(
        client,  # type: ignore[arg-type]
        normalized,
        model="llama3",
        runs=2,
        options=SeriesOptions(runs=2),
        thresholds=Thresholds(),
    )
    assert run.verdict == "fail"
    assert any("run 2" in reason and "parse_error" in reason for reason in run.reasons)


def test_worst_ordering() -> None:
    assert _worst(["pass", "warn"]) == "warn"
    assert _worst(["warn", "fail", "pass"]) == "fail"
    assert _worst([]) == "fail"
