#!/usr/bin/env bash
# GPU stand for dz2 (Linux): shared ollama with the infrastructure overlay +
# the Streamlit workbench.
#
#   ./scripts/up-gpu.sh              # build if needed, start the stand
#   ./scripts/up-gpu.sh --rebuild    # force project image rebuild
#
# NVIDIA passthrough is available on Linux (NVIDIA driver + Container Toolkit).
# Docker Desktop on macOS cannot pass an NVIDIA GPU through to Linux containers,
# so the script warns and starts the same stand on CPU there. On macOS you can
# use ./scripts/up-cpu.sh directly.
set -euo pipefail

scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$scripts_dir/_stand.sh" gpu "$@"
