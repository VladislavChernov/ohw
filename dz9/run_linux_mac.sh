#!/usr/bin/env bash
# Script: run_linux_mac.sh
# Description: Run the model-quality-lab pipeline on Linux/macOS via Docker.
# Usage:
#   ./run_linux_mac.sh
#   ./run_linux_mac.sh --dataset breast_cancer --model logistic_regression \
#       --train --predict --metrics --visualize
# Any CLI arguments are passed to model_quality_lab; with no arguments the
# full demo run (all datasets x all models) is executed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="ghcr.io/astral-sh/uv:python3.13-bookworm-slim"

if [ "$#" -eq 0 ]; then
    set -- --all-datasets --all-models --train --predict --metrics --visualize
fi

echo
echo "=========================================="
echo "model-quality-lab pipeline"
echo "=========================================="
echo "Repository: $REPO_ROOT"
echo "Image:      $IMAGE"
echo "Arguments:  $*"
echo

docker run --rm \
    -v "$REPO_ROOT:/app" \
    -w /app/dz9 \
    "$IMAGE" \
    sh -c 'uv pip install --system -r /app/dz9/requirements.txt >/dev/null && export PYTHONPATH=/app/dz9/src && exec python -m model_quality_lab "$@"' \
    _ "$@"

echo
echo "DONE. Plots are in dz9/output/, summary is printed above."
