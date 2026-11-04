from __future__ import annotations

from typing import Any

from .config import DEFAULT_MODEL_NAME, MODEL_MAX_ITER, RANDOM_STATE, SUPPORTED_MODELS


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

    # Features live on different scales (e.g. Pima: glucose vs. pedigree);
    # standardisation in a pipeline keeps both linear models comparable.
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if model_name == "perceptron":
        from sklearn.linear_model import Perceptron

        return make_pipeline(StandardScaler(), Perceptron(random_state=RANDOM_STATE))

    from sklearn.linear_model import LogisticRegression

    return make_pipeline(
        StandardScaler(),
        LogisticRegression(random_state=RANDOM_STATE, max_iter=MODEL_MAX_ITER),
    )