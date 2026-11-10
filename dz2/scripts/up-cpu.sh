#!/usr/bin/env bash
# CPU stand for dz2 (Linux / macOS): shared ollama on CPU + workbench UI.
#
#   ./scripts/up-cpu.sh              # build if needed, start the stand
#   ./scripts/up-cpu.sh --rebuild    # force project image rebuild
set -euo pipefail

scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$scripts_dir/_stand.sh" cpu "$@"
