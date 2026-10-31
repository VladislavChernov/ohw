#!/usr/bin/env bash
# Shared stand launcher for dz2 (Linux / macOS).
# Do not call directly: use up-cpu.sh or up-gpu.sh.
#
# What it does:
#   1. checks Docker (and NVIDIA for gpu mode);
#   2. builds the shared base image ohw-python:3.13 if it is missing;
#   3. starts the shared components declared in ../infra.yaml (ollama),
#      with the infra GPU overlay in gpu mode;
#   4. builds the single project image ohw/dz2:local (ui extra included);
#   5. starts the ui service and waits for it.
set -euo pipefail

mode="${1:-gpu}"
shift || true
rebuild=0

case "$mode" in
  cpu|gpu) ;;
  -h|--help)
    sed -n '2,12p' "$0"
    exit 0
    ;;
  *)
    echo "unknown mode: $mode (expected cpu or gpu)" >&2
    exit 2
    ;;
esac

for arg in "$@"; do
  case "$arg" in
    --rebuild) rebuild=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(cd "$project_dir/.." && pwd)"
infra_dir="$repo_dir/infra"
compose_file="$project_dir/compose.yaml"
manifest="$project_dir/infra.yaml"
base_image="ohw-python:3.13"
project_image="ohw/dz2:local"
ui_url="http://localhost:8501"

if [[ "$(uname -s)" == "Darwin" && "$mode" == "gpu" ]]; then
  echo "warning: Docker Desktop on macOS cannot pass an NVIDIA GPU to Linux" >&2
  echo "warning: containers; starting the stand on CPU instead." >&2
  mode="cpu"
fi

echo "=== dz2 stand: $mode ==="

command -v docker >/dev/null 2>&1 || {
  echo "docker not found. Install Docker Desktop / Docker Engine." >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "Docker daemon is not available. Start Docker." >&2
  exit 1
}

if [[ "$mode" == "gpu" ]]; then
  command -v nvidia-smi >/dev/null 2>&1 || {
    echo "GPU mode needs nvidia-smi (NVIDIA driver)." >&2
    exit 1
  }
  nvidia-smi -L >/dev/null 2>&1 || {
    echo "nvidia-smi cannot see a GPU." >&2
    exit 1
  }
  if ! docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"'; then
    echo "Docker has no nvidia runtime. Install NVIDIA Container Toolkit." >&2
    exit 1
  fi
fi

if ! docker image inspect "$base_image" >/dev/null 2>&1; then
  echo "Building shared base image $base_image (infra/python)..."
  docker build -t "$base_image" "$infra_dir/python"
fi

components="$(grep -E '^\s*-\s*[A-Za-z0-9_-]+\s*$' "$manifest" | awk '{print $2}')"
if [[ -z "$components" ]]; then
  echo "No components declared in $manifest" >&2
  exit 1
fi

infra_cmd=(docker compose -f "$infra_dir/compose.yaml")
if [[ "$mode" == "gpu" ]]; then
  infra_cmd+=(-f "$infra_dir/compose.gpu.yaml")
fi
for component in $components; do
  infra_cmd+=(--profile "$component")
done
infra_cmd+=(up -d --force-recreate)

echo "Starting shared components: $(echo "$components" | tr '\n' ' ')"
"${infra_cmd[@]}"

if [[ $rebuild -eq 1 ]]; then
  echo "Removing old project image $project_image..."
  docker rmi --force "$project_image" >/dev/null 2>&1 || true
fi

echo "Building project image $project_image..."
docker build --build-arg "EXTRAS=--extra ui" -t "$project_image" "$project_dir"

echo "Starting the ui service..."
docker compose -f "$compose_file" up -d --no-build ui

echo "Waiting for ollama (ohw-ollama)..."
deadline=$((SECONDS + 360))
while (( SECONDS < deadline )); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ohw-ollama 2>/dev/null || true)"
  [[ "$status" == "healthy" ]] && break
  sleep 2
done
if [[ "${status:-}" != "healthy" ]]; then
  echo "ollama did not become healthy. Check: docker logs ohw-ollama" >&2
  exit 1
fi

echo "Waiting for dz2 ui ($ui_url)..."
deadline=$((SECONDS + 180))
until curl -fsS "$ui_url/_stcore/health" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "dz2 ui did not answer. Check: docker compose -f \"$compose_file\" logs ui" >&2
    exit 1
  fi
  sleep 2
done

echo
echo "Stand is up ($mode):"
echo "  UI:      $ui_url"
echo "  Ollama:  http://localhost:11434 (container ohw-ollama)"
echo "  CLI:     docker compose -f \"$compose_file\" run --rm app --count 10"
echo "  Stop:    ./scripts/down.sh [-i]"
