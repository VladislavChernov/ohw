"""Tests for chart generation (headless matplotlib)."""

from pathlib import Path

from src.models import PipelineData, QualityMetrics, ScenarioResult
from src.visualization.charts import create_all_charts


def _data() -> PipelineData:
    scenarios = []
    for index, overall in enumerate((0.6, 0.8), start=1):
        scenarios.append(
            ScenarioResult(
                filename=f"req-{index}.md",
                source_path=f"input/req-{index}.md",
                artifact_type="testcases",
                output_file=f"scenarios/req-{index}.md",
                rendered="BR-1",
                cases=[],
                prompt="prompt",
                attempts=1,
                quality=QualityMetrics(
                    readability=overall,
                    clarity_goals=overall - 0.1,
                    scenario_coverage=1.0,
                    consistency=0.7,
                    overall=overall,
                ),
                requirement_ids=["BR-1"],
                covered_requirements=["BR-1"],
                prompt_sha256="abc",
                runs=1,
            )
        )
    data = PipelineData(scenarios=scenarios)
    data.refresh_averages()
    return data


def test_all_charts_are_created(tmp_path: Path) -> None:
    charts = create_all_charts(_data(), tmp_path)
    assert set(charts) == {"boxplot", "radar", "per_scenario"}
    for relative in charts.values():
        assert (tmp_path / relative).is_file()


def test_no_charts_without_metrics(tmp_path: Path) -> None:
    assert create_all_charts(PipelineData(), tmp_path) == {}
