"""Unit tests for model-quality-lab package."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pytest

from model_quality_lab import config
from model_quality_lab.config import enabled_plot_metrics
from model_quality_lab.dataset import DatasetSplit, load_dataset
from model_quality_lab.metrics import (
    compute_confusion_matrix,
    compute_metrics,
    format_confusion_matrix,
)
from model_quality_lab.model import build_model
from model_quality_lab.persistence import load_model, save_model
from model_quality_lab.predict import predict
from model_quality_lab.train import train
from model_quality_lab.visualize import plot_comparison, plot_metrics


def test_load_dataset():
    """Test that dataset loads correctly and returns DatasetSplit."""
    ds = load_dataset("heart")
    assert isinstance(ds, DatasetSplit), "Should return DatasetSplit"
    assert ds.X_train.shape[0] > 0, "X_train should not be empty"
    assert ds.X_test.shape[0] > 0, "X_test should not be empty"
    assert ds.y_train.shape[0] > 0, "y_train should not be empty"
    assert ds.y_test.shape[0] > 0, "y_test should not be empty"
    # Features and labels must describe the same rows.
    assert ds.X_train.shape[0] == ds.y_train.shape[0]
    assert ds.X_test.shape[0] == ds.y_test.shape[0]
    assert ds.X_train.shape[1] == ds.X_test.shape[1], "Feature count must match"
    assert not np.isnan(ds.X_train).any(), "X_train should not contain NaN"
    # The configured 20% test share is respected (within one sample).
    total = ds.X_train.shape[0] + ds.X_test.shape[0]
    assert abs(ds.X_test.shape[0] / total - 0.2) < 0.01
    # Check label values are binary
    assert set(np.unique(ds.y_train)).issubset({0, 1}), "y_train should be binary"
    assert set(np.unique(ds.y_test)).issubset({0, 1}), "y_test should be binary"
    print("test_load_dataset PASSED")


def test_build_model():
    """Test that model builds correctly."""
    model = build_model()
    assert model is not None, "Model should not be None"
    print("test_build_model PASSED")


@pytest.mark.parametrize("model_name", ["logistic_regression", "perceptron"])
def test_train_model(model_name):
    """Test that the model actually fits the training data."""
    ds = load_dataset("breast_cancer")
    model = train(model_name, ds)
    assert model is not None, "Trained model should not be None"
    final_estimator = model.steps[-1][1]
    assert hasattr(final_estimator, "coef_"), "Trained model should have coefficients"
    train_accuracy = model.score(ds.X_train, ds.y_train)
    assert train_accuracy > 0.9, (
        f"{model_name} should fit the training set, got {train_accuracy:.3f}"
    )
    print(f"test_train_model[{model_name}] PASSED")


def test_predict():
    """Test that predictions can be made."""
    ds = load_dataset("heart")
    model = train("logistic_regression", ds)
    y_pred = predict(model, ds)
    assert len(y_pred) == len(ds.y_test), "Predictions should match test set size"
    assert set(np.unique(y_pred)).issubset({0, 1}), "Predictions should be binary"
    print("test_predict PASSED")


def test_compute_metrics():
    """Test metrics computation with known values."""
    y_true = np.array([1, 0, 1, 1, 0, 1])
    y_pred = np.array([1, 0, 0, 1, 0, 1])
    metrics = compute_metrics(y_true, y_pred)
    assert "accuracy" in metrics, "Should have accuracy"
    assert "precision" in metrics, "Should have precision"
    assert "recall" in metrics, "Should have recall"
    # Known calculation: TP=3, TN=2, FP=0, FN=1, N=6
    # accuracy = (3+2)/6 = 5/6 ≈ 0.833
    # precision = 3/(3+0) = 1.0
    # recall = 3/(3+1) = 0.75
    assert abs(metrics["accuracy"] - 5/6) < 0.01, f"accuracy should be ~0.833, got {metrics['accuracy']}"
    assert abs(metrics["precision"] - 1.0) < 0.01, f"precision should be 1.0, got {metrics['precision']}"
    assert abs(metrics["recall"] - 0.75) < 0.01, f"recall should be 0.75, got {metrics['recall']}"
    print("test_compute_metrics PASSED")


def test_compute_metrics_zero_denominator():
    """Test metrics with zero denominators (fail-safe)."""
    y_true = np.array([0, 0, 0])
    y_pred = np.array([0, 0, 0])
    # All negatives, no positives predicted -> precision denominator TP+FP = 0
    metrics = compute_metrics(y_true, y_pred)
    assert metrics["precision"] == 0.0, "precision should be 0.0 when TP+FP=0"
    assert metrics["recall"] == 0.0, "recall should be 0.0 when TP+FN=0"
    print("test_compute_metrics_zero_denominator PASSED")


def test_save_load_model():
    """Test model save and load roundtrip."""
    import tempfile
    ds = load_dataset("heart")
    model = train("logistic_regression", ds)
    
    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
        tmp_path = f.name
    
    try:
        save_model(model, tmp_path)
        loaded_model = load_model(tmp_path)
        # The loaded model must reproduce predictions exactly.
        before = predict(model, ds)
        after = predict(loaded_model, ds)
        assert np.array_equal(before, after), "Loaded model must predict identically"
        assert len(after) == len(ds.y_test)
        print("test_save_load_model PASSED")
    finally:
        import os
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_compute_confusion_matrix():
    """Test confusion matrix computation."""
    y_true = np.array([1, 0, 1, 1, 0, 1])
    y_pred = np.array([1, 0, 0, 1, 0, 1])
    tp, tn, fp, fn = compute_confusion_matrix(y_true, y_pred)
    assert tp == 3, f"TP should be 3, got {tp}"
    assert tn == 2, f"TN should be 2, got {tn}"
    assert fp == 0, f"FP should be 0, got {fp}"
    assert fn == 1, f"FN should be 1, got {fn}"
    print("test_compute_confusion_matrix PASSED")


def test_format_confusion_matrix():
    """Test console formatting of the confusion matrix."""
    text = format_confusion_matrix(tp=3, tn=2, fp=0, fn=1)
    assert "pred=1" in text, "Header should contain predicted columns"
    assert "actual=1" in text, "Table should contain actual rows"
    for cell in ("TP=3", "TN=2", "FP=0", "FN=1"):
        assert cell in text, f"Table should contain {cell}"
    print("test_format_confusion_matrix PASSED")


def test_plot_metrics(tmp_path):
    """Test metrics plotting."""
    metrics = {"accuracy": 0.85, "precision": 0.90, "recall": 0.80}
    path = plot_metrics(metrics, output_path=tmp_path / "metrics.png")
    assert path.exists(), "Metrics plot should be saved"
    print("test_plot_metrics PASSED")


def test_enabled_plot_metrics():
    """All three metrics are plotted by default."""
    assert enabled_plot_metrics() == ["accuracy", "precision", "recall"]
    print("test_enabled_plot_metrics PASSED")


def test_plot_metrics_respects_config(tmp_path, monkeypatch):
    """A metric disabled in PLOT_METRICS is not drawn, but plotting still works."""
    monkeypatch.setitem(config.PLOT_METRICS, "recall", False)
    assert enabled_plot_metrics() == ["accuracy", "precision"]
    path = plot_metrics(
        {"accuracy": 0.85, "precision": 0.90, "recall": 0.80},
        output_path=tmp_path / "metrics.png",
    )
    assert path.exists(), "Metrics plot should be saved"
    print("test_plot_metrics_respects_config PASSED")


def test_plot_comparison_rejects_all_disabled(monkeypatch):
    """Disabling every metric is a configuration error."""
    for name in list(config.PLOT_METRICS):
        monkeypatch.setitem(config.PLOT_METRICS, name, False)
    with pytest.raises(ValueError):
        plot_comparison(
            {"logistic_regression": {"accuracy": 0.9, "precision": 0.8, "recall": 0.85}}
        )
    print("test_plot_comparison_rejects_all_disabled PASSED")


def test_plot_comparison(tmp_path):
    """Test comparison metrics plotting across multiple models."""
    results = {
        "logistic_regression": {"accuracy": 0.9, "precision": 0.8, "recall": 0.85},
        "perceptron": {"accuracy": 0.7, "precision": 0.6, "recall": 0.75},
    }
    path = plot_comparison(results, output_path=tmp_path / "comparison.png")
    assert path.exists(), "Comparison plot should be saved"
    print("test_plot_comparison PASSED")


if __name__ == "__main__":
    test_load_dataset()
    test_build_model()
    test_train_model("logistic_regression")
    test_train_model("perceptron")
    test_predict()
    test_compute_metrics()
    test_compute_metrics_zero_denominator()
    test_save_load_model()
    test_compute_confusion_matrix()
    test_format_confusion_matrix()
    test_enabled_plot_metrics()
    print("\nFixture-free tests passed; run pytest for the full suite.")