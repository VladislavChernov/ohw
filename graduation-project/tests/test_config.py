"""Tests for parsing and resolving the overall score weights."""

import pytest

from src.config import (
    DEFAULT_QUALITY_WEIGHTS,
    METRIC_KEYS,
    format_quality_weights,
    parse_quality_weights,
    resolve_quality_weights,
    validate_quality_weights,
)

CANONICAL = (
    "readability=0.25, clarity_goals=0.25, "
    "scenario_coverage=0.30, consistency=0.20"
)


def test_parse_accepts_canonical_order() -> None:
    assert parse_quality_weights(CANONICAL) == DEFAULT_QUALITY_WEIGHTS


def test_parse_allows_spaces_and_custom_order() -> None:
    weights = parse_quality_weights(
        " consistency = 0.2 , readability = 0.2, "
        "scenario_coverage = 0.4, clarity_goals = 0.2 "
    )
    assert weights == {
        "readability": 0.2,
        "clarity_goals": 0.2,
        "scenario_coverage": 0.4,
        "consistency": 0.2,
    }
    assert tuple(weights) == METRIC_KEYS


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "пусты"),
        ("readability=0.5", "не заданы веса"),
        ("readability=0.25,clarity_goals=0.25,scenario_coverage=0.30", "не заданы"),
        ("readability=1.0,clarity_goals=0,scenario_coverage=0,unknown=0", "неизвестн"),
        ("readability=0.5,readability=0.5,clarity_goals=0,"
         "scenario_coverage=0,consistency=0", "дважды"),
        ("readability=abc,clarity_goals=0.25,scenario_coverage=0.3,"
         "consistency=0.2", "не является числом"),
        ("readability=-0.1,clarity_goals=0.4,scenario_coverage=0.5,"
         "consistency=0.2", "отрицательным"),
        ("readability=nan,clarity_goals=0.25,scenario_coverage=0.3,"
         "consistency=0.2", "конечным"),
        ("readability=inf,clarity_goals=0.25,scenario_coverage=0.3,"
         "consistency=0.2", "конечным"),
        ("readability=0.5,clarity_goals=0.3,scenario_coverage=0.3,"
         "consistency=0.2", "сумма весов"),
        ("readability", "неверный элемент"),
    ],
)
def test_parse_rejects_invalid_input(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_quality_weights(raw)


def test_validate_rejects_non_numeric_value() -> None:
    weights = dict(DEFAULT_QUALITY_WEIGHTS)
    weights["readability"] = "0.25"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="должен быть числом"):
        validate_quality_weights(weights)


def test_resolve_prefers_cli_over_env() -> None:
    env = {"GRADUATION_QUALITY_WEIGHTS": "readability=1,clarity_goals=0,"
           "scenario_coverage=0,consistency=0"}
    weights = resolve_quality_weights(CANONICAL, env=env)
    assert weights == DEFAULT_QUALITY_WEIGHTS


def test_resolve_uses_env_when_cli_is_missing() -> None:
    env = {"GRADUATION_QUALITY_WEIGHTS": "readability=0.4,clarity_goals=0.2,"
           "scenario_coverage=0.2,consistency=0.2"}
    weights = resolve_quality_weights(None, env=env)
    assert weights["readability"] == 0.4


def test_resolve_treats_empty_env_as_missing() -> None:
    assert resolve_quality_weights(None, env={"GRADUATION_QUALITY_WEIGHTS": "  "}) == (
        DEFAULT_QUALITY_WEIGHTS
    )


def test_resolve_falls_back_to_defaults() -> None:
    assert resolve_quality_weights(None, env={}) == DEFAULT_QUALITY_WEIGHTS


def test_format_quality_weights_is_canonical() -> None:
    assert format_quality_weights(DEFAULT_QUALITY_WEIGHTS) == CANONICAL
