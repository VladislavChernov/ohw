#!/usr/bin/env bash
# dz6/run.sh — start the shared infra (ollama) and run the test optimizer.
#
#   ./run.sh                  # CPU ollama, input from ./input
#   ./run.sh --gpu            # NVIDIA CUDA ollama
#   ./run.sh --rebuild        # rebuild the app image without cache
#   ./run.sh -i ./other/dir   # run against another directory of tests
#
# Requires Docker Desktop / Docker Engine with the compose plugin, and the
# shared infra catalog at ../infra (see ../infra/README.md).
set -euo pipefail

GPU=0
REBUILD=0
INPUT_FOLDER=""

usage() {
  echo "usage: $0 [--gpu] [--rebuild] [-i|--input FOLDER]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) GPU=1 ;;
    --rebuild) REBUILD=1 ;;
    -i|--input)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      INPUT_FOLDER="$2"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/infra"
cd "$SCRIPT_DIR"

if [[ ! -f "$INFRA_DIR/up.sh" ]]; then
  echo "Not found: $INFRA_DIR/up.sh. Run from D:/Otus/ohw/dz6." >&2
  exit 1
fi

if [[ -n "$INPUT_FOLDER" ]]; then
  [[ -d "$INPUT_FOLDER" ]] || { echo "No such directory: $INPUT_FOLDER" >&2; exit 2; }
  INPUT_FOLDER="$(cd "$INPUT_FOLDER" && pwd)"
fi

echo "==> [1/3] starting shared infra (ollama)"
INFRA_ARGS=("$SCRIPT_DIR")
if [[ $GPU -eq 1 ]]; then INFRA_ARGS+=("--gpu"); fi
bash "$INFRA_DIR/up.sh" "${INFRA_ARGS[@]}"

echo "==> waiting for ohw-ollama to become ready"
status="missing"
for _ in $(seq 1 180); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ohw-ollama 2>/dev/null || true)"
  if [[ "$status" == "healthy" || "$status" == "running" ]]; then
    break
  fi
  sleep 2
done
if [[ "$status" != "healthy" && "$status" != "running" ]]; then
  echo "ohw-ollama is not ready (status: $status)." >&2
  docker logs --tail 50 ohw-ollama >&2 || true
  exit 1
fi
echo "    ohw-ollama: $status"

echo "==> [2/3] building app image"
if [[ $REBUILD -eq 1 ]]; then
  docker compose build --no-cache app
else
  docker compose build app
fi

echo "==> [3/3] running the optimizer"
if [[ -n "$INPUT_FOLDER" ]]; then
  docker compose run --rm \
    -e INPUT_DIR=/data/tests \
    --mount "type=bind,source=$INPUT_FOLDER,target=/data/tests,readonly" \
    app
else
  docker compose run --rm app
fi

echo ""
echo "Done. Results in: $SCRIPT_DIR/output"
if compgen -G "$SCRIPT_DIR/output/*" > /dev/null; then
  ls -1 "$SCRIPT_DIR/output"
else
  echo "WARNING: output/ is empty — the run produced no artifacts." >&2
  exit 1
fi
