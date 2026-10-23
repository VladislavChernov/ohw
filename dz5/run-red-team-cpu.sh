#!/usr/bin/env sh
# CPU variant: Ollama runs without an NVIDIA GPU. Expect slow responses.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$ROOT/run-red-team.sh" --cpu "$@"
