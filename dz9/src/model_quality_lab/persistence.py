from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

from .config import MODEL_SAVE_PATH


def save_model(model: Any, path: Path | None = None) -> None:
    """
    Save a trained model to disk using pickle.

    Parameters
    ----------
    model : Any
        Trained model to save.
    path : Path, optional
        Path to save the model to. If None, uses default MODEL_SAVE_PATH.
    """
    save_path = Path(path) if path else MODEL_SAVE_PATH
    save_path.parent.mkdir(parents=True, exist_ok=True)

    with open(save_path, "wb") as f:
        pickle.dump(model, f)


def load_model(path: Path | None = None) -> Any:
    """
    Load a model from disk.

    Parameters
    ----------
    path : Path, optional
        Path to load the model from. If None, uses default MODEL_SAVE_PATH.

    Returns
    -------
    model : Any
        Loaded model.
    """
    load_path = Path(path) if path else MODEL_SAVE_PATH

    if not load_path.exists():
        raise FileNotFoundError(f"Model not found at {load_path}")

    with open(load_path, "rb") as f:
        return pickle.load(f)