"""llm-degradation-lab main entry point.

Run via: python -m llm_degradation health --model llama3 --json"""

from __future__ import annotations

from llm_degradation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
