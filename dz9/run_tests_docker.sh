#!/bin/bash
# Script: run_tests_docker.sh
# Description: Run model-quality-lab quality gate (ruff, mypy, pytest) in Docker
# Usage: bash run_tests_docker.sh

set -e

# Configuration — repo root mounted at /app inside the container
PROJECT_DIR="/c/Otus/ohw"
IMAGE="ghcr.io/astral-sh/uv:python3.13-bookworm-slim"
OUTPUT_FILE="test_output.log"

echo "========================================="
echo "Running model-quality-lab quality gate in Docker"
echo "========================================="
echo "Image: $IMAGE"
echo "Project dir: $PROJECT_DIR"
echo "Output file: $OUTPUT_FILE"
echo ""

docker run --rm \
  -v "$PROJECT_DIR":/app \
  -w /app \
  "$IMAGE" \
  sh -c "uv pip install --system -r dz9/requirements.txt && echo '--- CLEANING UNUSED CODE ---' && ruff check dz9/src/ --config dz9/pyproject.toml --fix --unsafe-fixes --ignore EXE002 && echo '--- RUNNING RUFF LINTER ---' && ruff check dz9/src/ --config dz9/pyproject.toml --ignore EXE002 && echo '--- RUNNING MYPY TYPE CHECK ---' && mypy dz9/src/ --config-file dz9/pyproject.toml --ignore-missing-imports && echo '--- RUNNING PYTEST ---' && python -m pytest dz9/tests/ -v -p no:cacheprovider" 2>&1 | tee "$OUTPUT_FILE"

EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "========================================="
if [ $EXIT_CODE -eq 0 ]; then
  echo "SUCCESS: Quality gate passed!"
else
  echo "FAILED: Quality gate exited with code $EXIT_CODE"
fi
echo "========================================="
exit $EXIT_CODE
