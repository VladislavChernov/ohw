"""Unit tests for dataset loading and splitting."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pytest

from model_quality_lab.config import TEST_SIZE
from model_quality_lab.dataset import DATASET_REGISTRY, load_dataset

UNIT = 0.01


def test_registry_has_example_datasets():
    """Registry exposes the built-in and shipped example datasets."""
    assert {"heart", "breast_cancer", "pima_indians"} <= set(DATASET_REGISTRY)


def test_breast_cancer_split():
    """The embedded scikit-learn breast cancer dataset loads and splits."""
    ds = load_dataset("breast_cancer")
    total = len(ds.y_train) + len(ds.y_test)
    assert total == 569
    assert set(np.unique(ds.y_train)) == {0, 1}


def test_pima_indians_split():
    """The shipped Pima Indians dataset loads and splits."""
    ds = load_dataset("pima_indians")
    total = len(ds.y_train) + len(ds.y_test)
    assert total == 768
    assert set(np.unique(ds.y_train)) == {0, 1}


def test_split_is_stratified():
    """Class shares in the test set match the configured test size."""
    ds = load_dataset("pima_indians", test_size=TEST_SIZE, random_state=42)
    test_ratio = len(ds.y_test) / (len(ds.y_train) + len(ds.y_test))

    for label in (0, 1):
        class_total = int((ds.y_train == label).sum() + (ds.y_test == label).sum())
        class_share = float((ds.y_test == label).sum()) / class_total
        assert abs(class_share - test_ratio) < UNIT


def test_split_is_deterministic():
    """The same seed produces identical splits."""
    first = load_dataset("heart", test_size=TEST_SIZE, random_state=42)
    second = load_dataset("heart", test_size=TEST_SIZE, random_state=42)
    assert np.array_equal(first.X_train, second.X_train)
    assert np.array_equal(first.y_train, second.y_train)
    assert np.array_equal(first.X_test, second.X_test)
    assert np.array_equal(first.y_test, second.y_test)


def test_csv_locator(tmp_path):
    """Third-party CSV files can be loaded through the csv: locator."""
    csv_file = tmp_path / "custom.csv"
    csv_file.write_text(
        "feature_1,feature_2,label\n"
        "0.0,1.0,0\n"
        "1.0,0.0,1\n"
        "1.0,1.0,1\n"
        "0.0,0.0,0\n"
        "0.5,1.5,0\n"
        "1.5,0.5,1\n"
        "1.5,1.5,1\n"
        "0.5,0.5,0\n",
        encoding="utf-8",
    )
    ds = load_dataset(f"csv:{csv_file}")
    assert len(ds.y_train) + len(ds.y_test) == 8
    assert set(np.unique(ds.y_train)) == {0, 1}


def test_missing_label_column(tmp_path):
    """A CSV without the label column is rejected."""
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("feature_1,feature_2\n0.0,1.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="label"):
        load_dataset(f"csv:{csv_file}")


def test_unknown_dataset_name():
    """An unknown registry key is rejected with a helpful message."""
    with pytest.raises(ValueError, match="Unknown dataset"):
        load_dataset("no_such_dataset")


def test_label_out_of_range(tmp_path):
    """Labels outside {0, 1} are rejected."""
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("feature_1,label\n0.0,0\n1.0,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="0, 1"):
        load_dataset(f"csv:{csv_file}")


def test_single_class_rejected(tmp_path):
    """A single-class dataset cannot be split with stratification."""
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("feature_1,label\n0.0,0\n1.0,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="both classes"):
        load_dataset(f"csv:{csv_file}")


def test_missing_csv_file(tmp_path):
    """A missing CSV file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_dataset(f"csv:{tmp_path / 'missing.csv'}")
