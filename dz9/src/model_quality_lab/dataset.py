from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from sklearn.model_selection import train_test_split

from .config import DATASET_DEFAULT_PATH, RANDOM_STATE, TEST_SIZE


class DatasetSplit:
    """Container for train/test dataset split."""
    def __init__(
        self,
        X_train: np.ndarray,
        X_test: np.ndarray,
        y_train: np.ndarray,
        y_test: np.ndarray,
    ):
        self.X_train = X_train
        self.X_test = X_test
        self.y_train = y_train
        self.y_test = y_test


def load_dataset(path: str | Path | None = None) -> DatasetSplit:
    """
    Load dataset from CSV file and split into train/test sets.

    CSV format: columns feature_1...feature_N + binary label column 'label'
    where label ∈ {0, 1}.

    Stratified split with fixed random_state for reproducibility.
    """
    csv_path = Path(path) if path else DATASET_DEFAULT_PATH

    if not csv_path.exists():
        raise FileNotFoundError(f"Dataset not found at {csv_path}")

    # Read CSV using built-in csv module
    features: list[list[float]] = []
    labels: list[int] = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)  # skip header
        # Find label column index
        try:
            label_idx = header.index("label")
        except ValueError:
            raise ValueError(
                f"CSV must contain 'label' column. Found headers: {header}"
            )

        for row in reader:
            if len(row) <= label_idx:
                continue  # skip incomplete rows
            # Extract features (all columns except label)
            feature_values = []
            for i, val in enumerate(row):
                if i == label_idx:
                    continue
                try:
                    feature_values.append(float(val))
                except ValueError:
                    feature_values.append(0.0)
            # If we have fewer features than expected, pad with zeros
            while len(feature_values) < 1:
                feature_values.append(0.0)
            features.append(feature_values)
            labels.append(int(row[label_idx]))

    # Convert to numpy arrays
    X = np.array(features, dtype=float)
    y = np.array(labels, dtype=int)

    if X.shape[0] == 0:
        raise ValueError(f"Dataset at {csv_path} contains no samples")

    if not np.all((y == 0) | (y == 1)):
        raise ValueError(f"Dataset labels must be in {{0, 1}}, got: {np.unique(y)}")

    if len(np.unique(y)) < 2:
        raise ValueError("Dataset must contain both classes for train/test split")

    # Stratified train/test split with fixed random_state for reproducibility
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=y,
    )

    return DatasetSplit(X_train, X_test, y_train, y_test)