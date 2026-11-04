from __future__ import annotations

from typing import Any

from .dataset import DatasetSplit
from .model import build_model


def train(model_name: str | None = None, 
          dataset: DatasetSplit | None = None) -> Any:
    """
    Train a model on the provided dataset.

    Parameters
    ----------
    model_name : str, optional
        Name of the model to build and train. If None, uses default.
    dataset : DatasetSplit, optional
        Dataset containing train data. If None, expects model to be
        trained later or data to be provided separately.

    Returns
    -------
    model : trained model
    """
    model = build_model(model_name)

    if dataset is not None:
        model.fit(dataset.X_train, dataset.y_train)

    return model