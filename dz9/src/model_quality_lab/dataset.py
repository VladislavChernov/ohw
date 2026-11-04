"""Dataset loading and stratified train/test splitting."""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from sklearn.datasets import load_breast_cancer
from sklearn.model_selection import train_test_split

from .config import DATA_DIR, DEFAULT_DATASET_NAME, RANDOM_STATE, TEST_SIZE

DatasetLoader = Callable[[], tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class DatasetSplit:
    """Container for a stratified train/test split."""

    X_train: np.ndarray
    X_test: np.ndarray
    y_train: np.ndarray
    y_test: np.ndarray


def _read_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a labeled CSV: feature columns plus a binary ``label`` column."""
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found at {path}")

    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"Dataset at {path} is empty") from exc

        try:
            label_idx = header.index("label")
        except ValueError as exc:
            raise ValueError(
                f"CSV must contain 'label' column. Found headers: {header}"
            ) from exc

        features: list[list[float]] = []
        labels: list[int] = []

        for row_number, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"Row {row_number}: expected {len(header)} columns, got {len(row)}"
                )
            try:
                labels.append(int(row[label_idx]))
                features.append(
                    [
                        float(value)
                        for index, value in enumerate(row)
                        if index != label_idx
                    ]
                )
            except ValueError as exc:
                raise ValueError(
                    f"Row {row_number}: non-numeric value in dataset"
                ) from exc

    if not features:
        raise ValueError(f"Dataset at {path} contains no samples")

    return np.asarray(features, dtype=float), np.asarray(labels, dtype=int)


def _load_heart() -> tuple[np.ndarray, np.ndarray]:
    """Load the small synthetic demo dataset shipped with the project."""
    return _read_csv(DATA_DIR / "heart.csv")


def _load_breast_cancer() -> tuple[np.ndarray, np.ndarray]:
    """Load the breast cancer dataset embedded in scikit-learn (offline)."""
    bunch = load_breast_cancer()
    return np.asarray(bunch.data, dtype=float), np.asarray(bunch.target, dtype=int)


def _load_pima_indians() -> tuple[np.ndarray, np.ndarray]:
    """Load the Pima Indians diabetes dataset shipped in ``dz9/data``."""
    return _read_csv(DATA_DIR / "pima_indians.csv")


DATASET_REGISTRY: Final[dict[str, DatasetLoader]] = {
    "heart": _load_heart,
    "breast_cancer": _load_breast_cancer,
    "pima_indians": _load_pima_indians,
}


def _validate_labels(y: np.ndarray, source: str) -> None:
    """Reject non-binary labels and single-class datasets."""
    unique = np.unique(y)
    if not np.all((y == 0) | (y == 1)):
        raise ValueError(f"Dataset '{source}' labels must be in {{0, 1}}, got: {unique}")
    if unique.size < 2:
        raise ValueError(
            f"Dataset '{source}' must contain both classes, got only: {unique}"
        )


def load_dataset(
    name: str = DEFAULT_DATASET_NAME,
    *,
    test_size: float = TEST_SIZE,
    random_state: int = RANDOM_STATE,
) -> DatasetSplit:
    """
    Load a dataset and split it into stratified train/test sets.

    Parameters
    ----------
    name : str
        Registry key (``heart``, ``breast_cancer``, ``pima_indians``) or a
        ``csv:<path>`` locator for a third-party CSV.
    test_size : float
        Share of samples reserved for the test set.
    random_state : int
        Seed used by the split for reproducibility.

    Returns
    -------
    DatasetSplit
        Train/test features and labels.

    Raises
    ------
    FileNotFoundError
        If the CSV file does not exist.
    ValueError
        If the dataset is unknown, empty, malformed or not binary.
    """
    if name.startswith("csv:"):
        path = Path(name.removeprefix("csv:"))
        X, y = _read_csv(path)
        source = str(path)
    else:
        loader = DATASET_REGISTRY.get(name)
        if loader is None:
            available = ", ".join(sorted(DATASET_REGISTRY))
            raise ValueError(
                f"Unknown dataset '{name}'. Available: {available}; "
                "or use csv:<path>"
            )
        X, y = loader()
        source = name

    _validate_labels(y, source)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=test_size,
        random_state=random_state,
        stratify=y,
    )

    return DatasetSplit(X_train, X_test, y_train, y_test)


__all__ = ["DATASET_REGISTRY", "DatasetSplit", "load_dataset"]
