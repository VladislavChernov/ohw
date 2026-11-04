"""Configuration for the model quality lab."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Project configuration
PROJECT_NAME = "model-quality-lab"
DATASET_DEFAULT_PATH = Path(__file__).parent.parent.parent / "data" / "heart.csv"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
MODEL_SAVE_PATH = OUTPUT_DIR / "model.pkl"

# Dataset configuration
DEFAULT_DATASET_NAME = "heart"

# Reproducibility seed
RANDOM_STATE = 42

# Model configuration
DEFAULT_MODEL_NAME = "logistic_regression"
SUPPORTED_MODELS = ["logistic_regression", "perceptron"]
MODEL_MAX_ITER = 5000

# Default test/train split ratio
TEST_SIZE = 0.2


@dataclass(frozen=True)
class QualityLabConfig:
    """Immutable runtime configuration for the pipeline."""

    seed: int = RANDOM_STATE
    test_size: float = TEST_SIZE
    model_name: str = DEFAULT_MODEL_NAME
    data_dir: Path = DATA_DIR
    output_dir: Path = OUTPUT_DIR
