from __future__ import annotations

from typing import Any

from numpy.typing import NDArray

from .dataset import DatasetSplit


def predict(model: Any, dataset: DatasetSplit | None = None, 
            X_test: NDArray | None = None) -> NDArray:
    """
    Make predictions using the trained model.

    Parameters
    ----------
    model : Any
        Trained model with a .predict() method.
    dataset : DatasetSplit, optional
        Dataset containing test features (used if X_test not provided).
    X_test : NDArray, optional
        Test feature matrix. If None, uses dataset.X_test.

    Returns
    -------
    y_pred : NDArray
        Predicted labels.
    """
    if X_test is None and dataset is not None:
        X_test = dataset.X_test

    if X_test is None:
        raise ValueError("Either dataset or X_test must be provided")

    return model.predict(X_test)