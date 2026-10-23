#!/usr/bin/env sh
# GPU variant: same as run-red-team.sh, but explicit about the NVIDIA path.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$ROOT/run-red-team.sh" --gpu "$@"
