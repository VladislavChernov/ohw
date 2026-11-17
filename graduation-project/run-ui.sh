#!/usr/bin/env sh
# Thin wrapper around: uv run python -m src.ui
# Opens http://localhost:8501 by default. Override with
# GRADUATION_UI_HOST and GRADUATION_UI_PORT.
set -eu
cd "$(dirname "$0")"
exec uv run python -m src.ui
