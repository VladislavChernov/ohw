import json

import pytest
from conftest import balanced_set, make_case, make_raw_cases

from ai_testgen.models import CaseType
from ai_testgen.validator import validate


def test_valid_balanced_set_passes() -> None:
    raw = make_raw_cases(balanced_set(10))

    result = validate(raw, 10)

    assert result.ok
    assert len(result.cases) == 10


def test_wrong_count_reports_issue() -> None:
    raw = make_raw_cases(balanced_set(8))

    result = validate(raw, 10)

    assert not result.ok
    assert any("expected exactly 10" in issue for issue in result.issues)


def test_all_positive_rejected_for_count_ge_2() -> None:
    cases = [make_case(id=f"TC-{i:02d}") for i in range(5)]
    result = validate(make_raw_cases(cases), 5)

    assert not result.ok
    assert any("no negative" in issue for issue in result.issues)


def test_all_negative_rejected_for_count_ge_2() -> None:
    cases = [make_case(id=f"TC-{i:02d}", type=CaseType.NEGATIVE) for i in range(5)]
    result = validate(make_raw_cases(cases), 5)

    assert not result.ok
    assert any("no positive" in issue for issue in result.issues)


def test_broken_json_reported() -> None:
    result = validate("{not json", 10)

    assert not result.ok
    assert any("not valid JSON" in issue for issue in result.issues)
    assert result.cases == []


def test_non_array_root_rejected() -> None:
    result = validate('{"unexpected": 1}', 1)

    assert not result.ok
    assert any("JSON array" in issue for issue in result.issues)


def test_wrapper_object_with_cases_key_accepted() -> None:
    raw = json.dumps({"cases": balanced_set(10)})

    result = validate(raw, 10)

    assert result.ok
    assert len(result.cases) == 10


def test_single_case_object_wrapped_into_list() -> None:
    raw = json.dumps(make_case())

    result = validate(raw, 1)

    assert result.ok
    assert len(result.cases) == 1


def test_invalid_item_field_collected_per_item() -> None:
    case = make_case(steps=[])
    result = validate(make_raw_cases([case]), 1)

    assert not result.ok
    assert any("#1 is invalid" in issue and "steps" in issue for issue in result.issues)


def test_unknown_type_rejected() -> None:
    case = make_case()
    case["type"] = "weird"
    result = validate(make_raw_cases([case]), 1)

    assert not result.ok


def test_negative_share_rule_for_count_ge_5() -> None:
    cases = balanced_set(10)[:2] + [make_case(id=f"TC-{i:02d}") for i in range(3, 10)]
    result = validate(make_raw_cases(cases), 10)

    assert not result.ok
    assert any("third" in issue for issue in result.issues)


@pytest.mark.parametrize("count", [3, 4])
def test_small_set_requires_at_least_one_negative(count: int) -> None:
    cases = [make_case(id=f"TC-{i:02d}", type=CaseType.POSITIVE) for i in range(count - 1)]
    cases.append(make_case(id="TC-99", type=CaseType.NEGATIVE))

    assert validate(make_raw_cases(cases), count).ok


def test_single_positive_case_allowed_for_count_one() -> None:
    assert validate(make_raw_cases([make_case()]), 1).ok
