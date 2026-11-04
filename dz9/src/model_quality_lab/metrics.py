from __future__ import annotations

import numpy as np


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """
    Compute classification metrics from true and predicted labels.

    Metrics calculated:
    - accuracy: (TP + TN) / N
    - precision: TP / (TP + FP), 0.0 if TP + FP == 0
    - recall: TP / (TP + FN), 0.0 if TP + FN == 0

    Parameters
    ----------
    y_true : np.ndarray
        True binary labels (0 or 1).
    y_pred : np.ndarray
        Predicted binary labels (0 or 1).

    Returns
    -------
    metrics : dict
        Dictionary with keys 'accuracy', 'precision', 'recall'.
    """
    if len(y_true) == 0 or len(y_pred) == 0:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0}

    # Ensure binary labels
    y_true = np.array(y_true, dtype=int)
    y_pred = np.array(y_pred, dtype=int)

    # Compute confusion matrix elements
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())

    n = len(y_true)

    # Calculate metrics with fail-safe zero handling
    accuracy = float((tp + tn) / n) if n > 0 else 0.0
    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0

    return {
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
    }


def compute_confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[int, int, int, int]:
    """
    Compute confusion matrix elements.

    Returns
    -------
    tp, tn, fp, fn : int
        True positive, true negative, false positive, false negative counts.
    """
    y_true = np.array(y_true, dtype=int)
    y_pred = np.array(y_pred, dtype=int)

    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())

    return tp, tn, fp, fn