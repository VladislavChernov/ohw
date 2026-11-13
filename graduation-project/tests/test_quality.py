"""Unit tests for the deterministic quality metrics."""

from src.models import QualityMetrics
from src.quality.assessor import (
    aggregate_metrics,
    assess_clarity_goals,
    assess_consistency,
    assess_internal_consistency,
    assess_quality,
    assess_readability,
    assess_scenario_coverage,
    covered_requirement_ids,
    extract_requirement_ids,
    strip_markdown,
    weighted_overall,
)


def test_readability_optimal_sentence_length() -> None:
    text = " ".join(["word"] * 17) + "."
    assert assess_readability(text) == 1.0


def test_readability_penalises_long_sentences() -> None:
    text = " ".join(["word"] * 77) + "."
    assert assess_readability(text) == 0.0


def test_readability_empty_text() -> None:
    assert assess_readability("   ") == 0.0


def test_readability_ignores_code_fences_and_tables() -> None:
    text = (
        "Короткое предложение из семи слов должно остаться читаемым без кода и таблиц.\n"
        "```\n" + "word " * 100 + "\n```\n"
        "|---|----------|\n"
    )
    assert assess_readability(text) > 0.8


def test_clarity_detects_all_categories() -> None:
    text = (
        "Система должна проверять форму. Ожидаем сообщение об ошибке. "
        "Пользователь не должен видеть пароль. Результат — успешный вход."
    )
    assert assess_clarity_goals(text) == 1.0


def test_clarity_empty_text() -> None:
    assert assess_clarity_goals("") == 0.0


def test_clarity_partial_categories() -> None:
    assert assess_clarity_goals("Система должна работать.") == 0.2


def test_extract_requirement_ids_sorted_and_unique() -> None:
    text = "BR-10, br-2, BR-2, BR-1"
    assert extract_requirement_ids(text) == ["BR-1", "BR-2", "BR-10"]


def test_coverage_none_without_ids() -> None:
    assert assess_scenario_coverage("BR-1", []) is None


def test_coverage_zero_when_output_has_no_ids() -> None:
    assert assess_scenario_coverage("текст без ссылок", ["BR-1"]) == 0.0


def test_coverage_partial_and_full() -> None:
    assert assess_scenario_coverage("BR-1 и BR-2", ["BR-1", "BR-2", "BR-3"]) == 0.667
    assert assess_scenario_coverage("BR-3, BR-1, BR-2", ["BR-1", "BR-2", "BR-3"]) == 1.0


def test_covered_requirements_order() -> None:
    assert covered_requirement_ids("BR-2 и BR-1", ["BR-1", "BR-2"]) == ["BR-1", "BR-2"]


def test_consistency_identical_runs() -> None:
    assert assess_consistency(["BR-1 шаг тест", "BR-1 шаг тест"]) == 1.0


def test_consistency_single_run_is_not_measurable() -> None:
    assert assess_consistency(["BR-1 шаг тест"]) is None


def test_consistency_different_runs_is_lower() -> None:
    assert assess_consistency(["красный шар", "синий куб"]) < 0.1


def test_internal_consistency_sequential_ids() -> None:
    assert assess_internal_consistency("BR-1 BR-2 BR-3") == 0.8


def test_internal_consistency_gap() -> None:
    assert assess_internal_consistency("BR-1 BR-3") == 0.5


def test_weighted_overall_renormalises_unknown_metric() -> None:
    score = weighted_overall(
        {
            "readability": 1.0,
            "clarity_goals": 0.5,
            "scenario_coverage": None,
            "consistency": 1.0,
        }
    )
    # (0.25*1 + 0.25*0.5 + 0.2*1) / 0.7
    assert score == 0.821


def test_weighted_overall_without_known_metrics() -> None:
    assert weighted_overall({"scenario_coverage": None}) == 0.0


def test_weighted_overall_uses_custom_weights() -> None:
    values = {
        "readability": 1.0,
        "clarity_goals": 0.0,
        "scenario_coverage": 0.0,
        "consistency": 0.0,
    }
    weights = {
        "readability": 0.1,
        "clarity_goals": 0.2,
        "scenario_coverage": 0.3,
        "consistency": 0.4,
    }
    assert weighted_overall(values, weights) == 0.1


def test_assess_quality_uses_custom_weights() -> None:
    metrics = assess_quality(
        "BR-1 BR-2",
        ["BR-1", "BR-2"],
        weights={
            "readability": 1.0,
            "clarity_goals": 0.0,
            "scenario_coverage": 0.0,
            "consistency": 0.0,
        },
    )
    assert metrics.overall == metrics.readability


def test_assess_quality_prefers_repeat_runs() -> None:
    metrics = assess_quality(
        "BR-1: система должна работать.",
        ["BR-1"],
        generations=["BR-1 система должна работать", "BR-1 система должна работать"],
    )
    assert metrics.consistency == 1.0
    assert metrics.scenario_coverage == 1.0


def test_assess_quality_uses_internal_heuristic_for_one_run() -> None:
    metrics = assess_quality("BR-1 BR-2", ["BR-1", "BR-2"])
    assert metrics.consistency == 0.8


def test_strip_markdown_removes_table_rules() -> None:
    assert strip_markdown("| a | b |\n|---|---|\n| 1 | 2 |") == "a b 1 2"


def test_aggregate_metrics_keeps_none_for_missing_data() -> None:
    result = aggregate_metrics(
        [
            QualityMetrics(readability=0.5, scenario_coverage=None),
            QualityMetrics(readability=1.0, scenario_coverage=1.0),
        ]
    )
    assert result["readability"] == 0.75
    assert result["scenario_coverage"] == 1.0
    assert result["consistency"] is None
