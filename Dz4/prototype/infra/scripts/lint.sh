#!/bin/sh
# Dz4 verify gate inside ohw/dz4-dev container: ruff + format + mypy.
cd /app
uv run --no-sync ruff check .
echo "RUFF_CHECK_EXIT=$?"
uv run --no-sync ruff format --check .
echo "RUFF_FMT_EXIT=$?"
uv run --no-sync mypy
echo "MYPY_EXIT=$?"
