"""Charts for the four quality metrics (headless matplotlib)."""

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (backend must be selected first)

from src.models import METRIC_KEYS, PipelineData, QualityMetrics  # noqa: E402

METRIC_LABELS: dict[str, str] = {
    "readability": "Readability",
    "clarity_goals": "Clarity of goals",
    "scenario_coverage": "Scenario coverage",
    "consistency": "Consistency",
}

CHART_FILES: dict[str, str] = {
    "boxplot": "metrics_boxplot.png",
    "radar": "radar_chart.png",
    "per_scenario": "per_scenario_scores.png",
}


def _values(data: PipelineData, key: str) -> list[float]:
    return [
        value
        for value in (getattr(scenario.quality, key) for scenario in data.scenarios)
        if value is not None
    ]


def _known_metrics(data: PipelineData) -> list[str]:
    return [key for key in METRIC_KEYS if _values(data, key)]


def create_boxplot(data: PipelineData, charts_dir: Path) -> Path | None:
    """Boxplot of each metric across scenarios."""
    series = [(key, _values(data, key)) for key in _known_metrics(data)]
    if not series:
        return None
    figure, axes = plt.subplots(figsize=(9, 5.5))
    axes.boxplot(
        [values for _, values in series],
        tick_labels=[METRIC_LABELS[key] for key, _ in series],
    )
    axes.set_title("Распределение метрик качества по сценариям")
    axes.set_ylabel("Score (0–1)")
    axes.set_ylim(0, 1)
    axes.grid(axis="y", alpha=0.3)
    figure.tight_layout()
    output = charts_dir / CHART_FILES["boxplot"]
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output


def create_radar_chart(data: PipelineData, charts_dir: Path) -> Path | None:
    """Radar chart of average metrics; needs at least three axes."""
    averages = {
        key: data.averages.get(key)
        for key in METRIC_KEYS
        if data.averages.get(key) is not None
    }
    if len(averages) < 3:
        return None
    labels = [METRIC_LABELS[key] for key in averages]
    values: list[float] = []
    for value in averages.values():
        if value is not None:
            values.append(value)
    angles = [index * 2 * math.pi / len(labels) for index in range(len(labels))]
    angles += angles[:1]
    values += values[:1]

    figure, axes = plt.subplots(figsize=(6, 6), subplot_kw={"polar": True})
    axes.plot(angles, values, "o-", linewidth=2)
    axes.fill(angles, values, alpha=0.25)
    axes.set_xticks(angles[:-1], labels)
    axes.set_ylim(0, 1)
    axes.set_title("Средние метрики качества")
    figure.tight_layout()
    output = charts_dir / CHART_FILES["radar"]
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output


def create_per_scenario_chart(data: PipelineData, charts_dir: Path) -> Path | None:
    """Grouped bars with one group per scenario."""
    scenarios = [
        scenario
        for scenario in data.scenarios
        if any(getattr(scenario.quality, key) is not None for key in METRIC_KEYS)
    ]
    if not scenarios:
        return None
    metrics = _known_metrics(data)
    if not metrics:
        return None

    positions = list(range(len(scenarios)))
    width = 0.8 / len(metrics)
    figure, axes = plt.subplots(figsize=(max(9, len(scenarios) * 1.6), 5.5))
    for index, key in enumerate(metrics):
        offsets = [position + index * width for position in positions]
        heights: list[float] = []
        for scenario in scenarios:
            value: float | None = getattr(scenario.quality, key)
            heights.append(float(value) if value is not None else float("nan"))
        axes.bar(offsets, heights, width=width, label=METRIC_LABELS[key])
    axes.set_xticks(
        [position + width * (len(metrics) - 1) / 2 for position in positions],
        [scenario.filename for scenario in scenarios],
        rotation=30,
        ha="right",
    )
    axes.set_ylim(0, 1)
    axes.set_ylabel("Score (0–1)")
    axes.set_title("Метрики качества по каждому сценарию")
    axes.legend(ncol=2, fontsize=8)
    axes.grid(axis="y", alpha=0.3)
    figure.tight_layout()
    output = charts_dir / CHART_FILES["per_scenario"]
    figure.savefig(output, dpi=150)
    plt.close(figure)
    return output


def create_all_charts(data: PipelineData, output_dir: Path) -> dict[str, str]:
    """Create every applicable chart and return relative paths by key."""
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    created: dict[str, str] = {}
    for key, factory in (
        ("boxplot", create_boxplot),
        ("radar", create_radar_chart),
        ("per_scenario", create_per_scenario_chart),
    ):
        path = factory(data, charts_dir)
        if path is not None:
            created[key] = f"charts/{path.name}"
    return created


def empty_metrics() -> QualityMetrics:
    """Convenience for callers that need a blank metric object."""
    return QualityMetrics()
