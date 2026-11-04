from __future__ import annotations

import argparse

from numpy.typing import NDArray

from .config import (
    DEFAULT_DATASET_NAME,
    DEFAULT_MODEL_NAME,
    OUTPUT_DIR,
    RANDOM_STATE,
    SUPPORTED_MODELS,
    TEST_SIZE,
)
from .dataset import DatasetSplit, load_dataset
from .metrics import compute_metrics
from .model import build_model
from .persistence import load_model, save_model
from .predict import predict
from .train import train
from .visualize import plot_comparison, plot_metrics

__all__ = [
    "DEFAULT_DATASET_NAME",
    "DEFAULT_MODEL_NAME",
    "RANDOM_STATE",
    "TEST_SIZE",
    "DatasetSplit",
    "build_model",
    "compute_metrics",
    "load_dataset",
    "load_model",
    "plot_comparison",
    "plot_metrics",
    "predict",
    "save_model",
    "train",
]

EXAMPLE_DATASETS = ["breast_cancer", "pima_indians"]


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the pipeline."""
    parser = argparse.ArgumentParser(
        description="Model quality lab: end-to-end ML pipeline"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL_NAME,
        choices=SUPPORTED_MODELS,
        help=f"Model to use. Choices: {SUPPORTED_MODELS}. Default: {DEFAULT_MODEL_NAME}",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=DEFAULT_DATASET_NAME,
        help=(
            "Dataset registry key (heart, breast_cancer, pima_indians) "
            "or a csv:<path> locator."
        ),
    )
    parser.add_argument(
        "--all-datasets",
        action="store_true",
        help="Run the pipeline sequentially for breast_cancer and pima_indians.",
    )
    parser.add_argument(
        "--test-size",
        type=float,
        default=TEST_SIZE,
        help=f"Test split ratio. Default: {TEST_SIZE}",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=RANDOM_STATE,
        help=f"Random state for reproducibility. Default: {RANDOM_STATE}",
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="Train the model.",
    )
    parser.add_argument(
        "--predict",
        action="store_true",
        help="Run predictions on test set.",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Compute and print metrics.",
    )
    parser.add_argument(
        "--save-model",
        action="store_true",
        help="Save trained model to disk.",
    )
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Plot and save metrics visualization.",
    )
    parser.add_argument(
        "--all-models",
        action="store_true",
        help="Run all supported models sequentially and generate comparison.",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> None:
    """Main entry point for the CLI pipeline."""
    parsed = parse_args(args)

    dataset_names = EXAMPLE_DATASETS if parsed.all_datasets else [parsed.dataset]
    models_to_run = SUPPORTED_MODELS if parsed.all_models else [parsed.model]

    print("=" * 60)
    print("Model-Quality-Lab Pipeline")
    print("=" * 60)
    print(f"Random state: {parsed.random_state}")
    print(f"Test size: {parsed.test_size}")
    print(f"Model(s): {models_to_run}")
    print(f"Dataset(s): {dataset_names}")
    print("=" * 60)

    # Track results for comparison (key: model name or dataset:model)
    results: dict[str, dict] = {}

    for dataset_name in dataset_names:
        print(f"\nLoading dataset: {dataset_name}")
        dataset = load_dataset(
            dataset_name,
            test_size=parsed.test_size,
            random_state=parsed.random_state,
        )

        for model_name in models_to_run:
            result_key = (
                model_name if len(dataset_names) == 1 else f"{dataset_name}:{model_name}"
            )

            print(f"\n{'=' * 40}")
            print(f"Dataset: {dataset_name} | model: {model_name}")
            print(f"{'=' * 40}")

            # Build model
            model = build_model(model_name)

            # Train model
            if parsed.train:
                print("Training...")
                model = train(model_name=model_name, dataset=dataset)
                print("Training completed!")

                # Save model if requested
                if parsed.save_model:
                    save_path = OUTPUT_DIR / f"model_{dataset_name}_{model_name}.pkl"
                    save_model(model, save_path)
                    print(f"Model saved to: {save_path}")

            # Run predictions if requested
            y_pred: NDArray | None = None
            if parsed.predict or parsed.metrics or parsed.visualize:
                print("Running predictions...")
                y_pred = predict(model, dataset=dataset)
                print(f"Predictions shape: {len(y_pred)}")

            # Compute metrics if requested
            metrics_dict: dict[str, float] | None = None
            if parsed.metrics or parsed.visualize:
                print("Computing metrics...")
                if (
                    y_pred is not None
                    and dataset.y_test is not None
                    and len(dataset.y_test) > 0
                ):
                    metrics_dict = compute_metrics(dataset.y_test, y_pred)
                    print(f"Metrics: {metrics_dict}")

            # Visualize if requested
            if parsed.visualize:
                print("Generating visualization...")
                if metrics_dict is not None:
                    plot_path = OUTPUT_DIR / f"metrics_{dataset_name}_{model_name}.png"
                    plot_file = plot_metrics(metrics_dict, output_path=plot_path)
                    print(f"Plot saved to: {plot_file}")

            # Store results
            results[result_key] = {
                "metrics": metrics_dict,
                "predictions": y_pred,
                "model": model,
            }

    # Summary
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETED")
    print("=" * 60)

    if len(results) > 1:
        comparison: dict[str, dict[str, float]] = {}
        for name, res in results.items():
            if res.get("metrics"):
                comparison[name] = res["metrics"]

        if comparison:
            comparison_path = OUTPUT_DIR / "metrics_comparison.png"
            comparison_file = plot_comparison(comparison, output_path=comparison_path)
            print(f"Comparison plot saved to: {comparison_file}")

        print("\nComparison summary:")
        for name, res in results.items():
            m = res.get("metrics")
            if m:
                print(
                    f"  {name}: accuracy={float(m.get('accuracy', 0.0)):.3f}, "
                    f"precision={float(m.get('precision', 0.0)):.3f}, "
                    f"recall={float(m.get('recall', 0.0)):.3f}"
                )


if __name__ == "__main__":
    main()
