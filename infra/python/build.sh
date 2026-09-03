#!/usr/bin/env bash
# Builds the shared python base image used by homework app Dockerfiles.
# Cross-platform (Linux / macOS / Windows via Git Bash or WSL).
# Usage (from anywhere):  bash /path/to/infra/python/build.sh [--no-cache]
set -euo pipefail

image="ohw-python:3.13"
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Building $image from $dir ..."
if [[ "${1:-}" == "--no-cache" ]]; then
  docker build --no-cache -t "$image" "$dir"
else
  docker build -t "$image" "$dir"
fi
echo "OK: $image"
