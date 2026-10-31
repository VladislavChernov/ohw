#!/usr/bin/env bash
# Stops the dz2 stand (Linux / macOS).
#
#   ./scripts/down.sh        # project ui only
#   ./scripts/down.sh -i     # also stop shared ollama (other projects too!)
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(cd "$project_dir/.." && pwd)"
with_infra=0

for arg in "$@"; do
  case "$arg" in
    -i|--infra) with_infra=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

docker compose -f "$project_dir/compose.yaml" down

if [[ $with_infra -eq 1 ]]; then
  docker compose -f "$repo_dir/infra/compose.yaml" down
fi

echo "dz2 stand stopped."
