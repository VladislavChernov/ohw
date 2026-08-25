
from conftest import balanced_set, make_case

from ai_testgen.models import CaseType, TestCase
from ai_testgen.renderer import render


def build_cases(count: int) -> list[TestCase]:
    return [TestCase.model_validate(item) for item in balanced_set(count)]


def test_renders_exactly_n_cases() -> None:
    cases = build_cases(10)

    output = render(cases, "https://example.com", "input.md")

    assert output.count("### TC-") == 10


def test_has_positive_and_negative_sections() -> None:
    output = render(build_cases(10), None, "input.md")

    assert "## Позитивные сценарии" in output
    assert "## Негативные сценарии" in output


def test_summary_counts_match() -> None:
    output = render(build_cases(10), "https://example.com", "doc.md")

    assert "**Всего кейсов:** 10" in output
    assert "позитивных: 6" in output
    assert "негативных: 4" in output
    assert "**Тестируемый сайт:** https://example.com" in output


def test_case_contains_steps_table_and_expected() -> None:
    case = TestCase.model_validate(
        make_case(steps=["open page", "fill form", "submit"])
    )

    output = render([case], None, "x.md")

    assert "| № | Действие |" in output
    assert "| 1 | open page |" in output
    assert "| 3 | submit |" in output
    assert "**Ожидаемый результат:** expected result" in output


def test_negative_cases_marked_in_russian() -> None:
    case = TestCase.model_validate(make_case(type=CaseType.NEGATIVE))

    output = render([case], None, "x.md")

    assert "**Тип:** негативный" in output

def test_requirement_reference_rendered() -> None:
    case = TestCase.model_validate({**make_case(), "requirement": "BR-3"})

    output = render([case], None, "x.md")

    assert "**Проверяет требование:** BR-3" in output


def test_requirement_absent_no_line() -> None:
    case = TestCase.model_validate(make_case())

    output = render([case], None, "x.md")

    assert "Проверяет требование" not in output
