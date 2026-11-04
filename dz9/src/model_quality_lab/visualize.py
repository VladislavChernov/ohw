from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless backend

from pathlib import Path

import matplotlib.pyplot as plt

from .config import OUTPUT_DIR


def plot_metrics(metrics: dict[str, float], 
                 output_path: Path | None = None) -> Path:
    """
    Plot classification metrics and save to file.

    Parameters
    ----------
    metrics : Dict[str, float]
        Dictionary with keys 'accuracy', 'precision', 'recall'.
    output_path : Path, optional
        Path to save the plot to. If None, uses default output path.

    Returns
    -------
    Path
        Path to the saved plot file.
    """
    if output_path is None:
        output_path = OUTPUT_DIR / "metrics_plot.png"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    labels = list(metrics.keys())
    values = list(metrics.values())

    plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, values, color=["skyblue", "lightgreen", "salmon"])
    plt.ylim(0, 1.1)
    plt.title("Classification Metrics")
    plt.ylabel("Score")

    # Add value labels on top of bars
    for bar in bars:
        height = bar.get_height()
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            height,
            f"{height:.2f}",
            ha="center",
            va="bottom",
        )

    plt.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close()

    return output_path


def plot_comparison(
    results: dict[str, dict[str, float]],
    output_path: Path | None = None,
) -> Path:
    """
    Plot a comparison of metrics across multiple trained models.

    Parameters
    ----------
    results : dict[str, dict[str, float]]
        Mapping of model name -> metrics dict with keys
        'accuracy', 'precision', 'recall'.
    output_path : Path, optional
        Path to save the plot to. If None, uses default output path.

    Returns
    -------
    Path
        Path to the saved plot file.

    Raises
    ------
    ValueError
        If `results` is empty.
    """
    if not results:
        raise ValueError("results must contain at least one model")

    if output_path is None:
        output_path = OUTPUT_DIR / "metrics_comparison.png"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    metrics_keys = ["accuracy", "precision", "recall"]
    models = list(results.keys())
    n_models = len(models)
    width = 0.25
    offsets = [float(i) for i in range(n_models)]

    _fig, ax = plt.subplots(figsize=(10, 6))

    for idx, metric in enumerate(metrics_keys):
        values = [float(results[model][metric]) for model in models]
        ax.bar(
            [offset + idx * width for offset in offsets],
            values,
            width,
            label=metric,
        )

    ax.set_xticks(
        [offset + (len(metrics_keys) - 1) * width / 2 for offset in offsets],
    )
    ax.set_xticklabels(models, rotation=20, ha="right")
    ax.set_ylim(0, 1.1)
    ax.set_title("Model Comparison")
    ax.set_xlabel("Model")
    ax.set_ylabel("Score")
    ax.legend(title="Metric")

    plt.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close()

    return output_path