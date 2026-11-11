#!/usr/bin/env python3
"""Compatibility launcher: ``python scripts/cli.py``."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.workflow.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
