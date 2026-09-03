#!/usr/bin/env bash
# infra/up.sh — start shared components declared by a project's infra.yaml.
# Cross-platform (Linux / macOS / Windows via Git Bash or WSL).
#
#   ./up.sh <project-path>          # start components from <project>/infra.yaml
#   ./up.sh <project-path> --gpu    # NVIDIA CUDA overlay
#   ./up.sh <project-path> --amd    # AMD ROCm overlay
#
# Equivalent to the retired up.ps1.
set -euo pipefail

project=""
gpu=0
amd=0
for arg in "$@"; do
  case "$arg" in
    --gpu) gpu=1 ;;
    --amd) amd=1 ;;
    -h|--help) echo "usage: $0 <project-path> [--gpu] [--amd]"; exit 0 ;;
    *) project="$arg" ;;
  esac
done

if [[ -z "$project" ]]; then
  echo "usage: $0 <project-path> [--gpu] [--amd]" >&2
  exit 1
fi

manifest="$project/infra.yaml"
if [[ ! -f "$manifest" ]]; then
  echo "No infra.yaml found in $project" >&2
  exit 1
fi

# Parse the flat `components:` list (one "- name" per line).
components=$(grep -E '^\s*-\s*[A-Za-z0-9_-]+\s*$' "$manifest" | awk '{print $2}' || true)
if [[ -z "$components" ]]; then
  echo "No components declared in $manifest — nothing to start."
  exit 0
fi

dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd=(docker compose -f "$dir/compose.yaml")
if [[ $gpu -eq 1 ]]; then cmd+=(-f "$dir/compose.gpu.yaml"); fi
if [[ $amd -eq 1 ]]; then cmd+=(-f "$dir/compose.amd.yaml"); fi
for c in $components; do cmd+=(--profile "$c"); done
cmd+=(up -d)

mode=""
[[ $gpu -eq 1 ]] && mode="$mode GPU"
[[ $amd -eq 1 ]] && mode="$mode AMD"
echo "Starting shared components ($mode): $(echo $components | tr '\n' ' ')"
exec "${cmd[@]}"
