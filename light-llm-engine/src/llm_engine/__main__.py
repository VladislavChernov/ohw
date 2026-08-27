"""Entry point for `python -m llm_engine`."""

import sys

from llm_engine.cli import main

if __name__ == "__main__":
    sys.exit(main())