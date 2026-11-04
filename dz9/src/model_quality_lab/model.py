from __future__ import annotations

from typing import Any

from .config import DEFAULT_MODEL_NAME, RANDOM_STATE, SUPPORTED_MODELS


def build_model(name: str | None = None) -> Any:
    """
    Build and return a machine learning model.

    Parameters
    ----------
    name : str, optional
        Name of the model to build. If None, uses DEFAULT_MODEL_NAME.

    Returns
    -------
    model : fitted or unfitted sklearn-compatible model
    """
    model_name = name if name else DEFAULT_MODEL_NAME

    if model_name not in SUPPORTED_MODELS:
        raise ValueError(
            f"Model '{model_name}' not supported. "
            f"Choose from: {SUPPORTED_MODELS}"
        )

    if model_name == "perceptron":
        from sklearn.linear_model import Perceptron

        return Perceptron(random_state=RANDOM_STATE)
    else:
        from sklearn.linear_model import LogisticRegression

        return LogisticRegression(random_state=RANDOM_STATE, max_iter=1000)