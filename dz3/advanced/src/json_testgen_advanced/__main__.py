"""Run the advanced CLI as ``python -m json_testgen_advanced`` (container entrypoint)."""

from json_testgen_advanced.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
