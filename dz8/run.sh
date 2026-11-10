#!/usr/bin/env bash
# One-command launcher for the LLM Degradation Lab (Linux / macOS).
#
# Starts the shared dz2 stand (ohw-ollama + dz2-ui-1), makes sure llama3 is
# present, optionally runs the lab health checks and prints the UI URL.
# The lab itself lives inside the dz2 UI:
#   http://localhost:8501 -> sidebar -> "Model check".
#
# No new containers, ports or networks are created: the script reuses the dz2
# stand scripts from the sibling dz2 folder and the shared infra/ catalog.
#
# Usage:
#   ./run.sh              # CPU stand (Linux and macOS)
#   ./run.sh --gpu        # Linux + NVIDIA (macOS falls back to CPU with a warning)
#   ./run.sh --rebuild    # rebuild the project image
#   ./run.sh --check      # also run health + api-check against llama3
#   ./run.sh --no-pull    # do not pull llama3 (offline demo)
set -euo pipefail

dz8_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$dz8_dir/.." && pwd)"
dz2_dir="$repo_root/dz2"
compose_file="$dz2_dir/compose.yaml"
ui_url="http://localhost:8501"
model="llama3"
mode="cpu"
rebuild=0
check=0
pull=1

for arg in "$@"; do
  case "$arg" in
    --gpu) mode="gpu" ;;
    --cpu) mode="cpu" ;;
    --rebuild) rebuild=1 ;;
    --check) check=1 ;;
    --no-pull) pull=0 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

echo "=== dz8 lab launcher ==="

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found. Install Docker Desktop / Docker Engine." >&2
  exit 1
fi

if [[ ! -f "$compose_file" || ! -f "$repo_root/infra/compose.yaml" ]]; then
  echo "The lab needs the dz2 and infra folders next to dz8." >&2
  echo "Restore them with: git sparse-checkout set dz8 dz2 infra" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not available. Start Docker." >&2
  exit 1
fi

up_script="$dz2_dir/scripts/up-$mode.sh"
if [[ ! -f "$up_script" ]]; then
  echo "Stand script not found: $up_script" >&2
  exit 1
fi

stand_healthy() {
  local status
  status="$(docker inspect --format \
    '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    ohw-ollama 2>/dev/null || true)"
  [[ "$status" == "healthy" ]] || return 1
  status="$(docker inspect --format '{{.State.Status}}' dz2-ui-1 2>/dev/null || true)"
  [[ "$status" == "running" ]] || return 1
  curl -fsS "$ui_url/_stcore/health" >/dev/null 2>&1
}

stand_mode() {
  if docker inspect ohw-ollama --format '{{json .HostConfig.DeviceRequests}}' 2>/dev/null \
    | grep -q '"Driver":"nvidia"'; then
    echo "gpu"
  else
    echo "cpu"
  fi
}

if stand_healthy && [[ "$(stand_mode)" == "$mode" ]] && [[ $rebuild -eq 0 ]]; then
  echo "The stand is already up ($mode); skipping the dz2 up-script (use --rebuild to restart)."
elif [[ $rebuild -eq 1 ]]; then
  bash "$up_script" --rebuild
else
  bash "$up_script"
fi

if [[ $pull -eq 1 ]]; then
  if docker exec ohw-ollama ollama list 2>/dev/null | grep -q '^llama3'; then
    echo "Model $model is already installed in ohw-ollama."
  else
    echo "Pulling $model into ohw-ollama (one-time download, several GB)..."
    docker exec ohw-ollama ollama pull "$model"
  fi
else
  echo "Skipping the model check (--no-pull)."
fi

status=0
if [[ $check -eq 1 ]]; then
  echo "Checking the lab against $model (the first call loads the model)..."
  for command in health api-check; do
    if docker compose -f "$compose_file" exec -T ui \
      python -m llm_degradation "$command" --model "$model" --json; then
      echo "  $command ok"
    else
      echo "  $command failed" >&2
      status=1
    fi
  done
fi

echo
echo "The lab is ready:"
echo "  UI:      $ui_url  (sidebar -> 'Model check')"
echo "  Model:   $model"
echo "  Output:  $dz8_dir/output"
echo "  Docs:    $dz8_dir/docs/user-guide.md"
echo "  Stop:    $dz2_dir/scripts/down.sh [-i]"
echo
echo "The first answer can take 2-3 minutes while the model is loaded."

exit $status
