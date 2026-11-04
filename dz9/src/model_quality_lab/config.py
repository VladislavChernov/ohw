from __future__ import annotations

from pathlib import Path

# Project configuration
PROJECT_NAME = "model-quality-lab"
DATASET_DEFAULT_PATH = Path(__file__).parent.parent.parent / "data" / "heart.csv"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
MODEL_SAVE_PATH = OUTPUT_DIR / "model.pkl"

# Reproducibility seed
RANDOM_STATE = 42

# Model configuration
DEFAULT_MODEL_NAME = "logistic_regression"
SUPPORTED_MODELS = ["logistic_regression", "perceptron"]

# Default test/train split ratio
TEST_SIZE = 0.25