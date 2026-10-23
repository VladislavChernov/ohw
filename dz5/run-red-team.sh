#!/usr/bin/env sh
# Cross-platform POSIX shell launcher for the dz5 red-team run.
# Works on Linux, macOS, WSL and Git Bash. Native Windows: run-red-team.ps1.
# Default is GPU; use --cpu on a machine without an NVIDIA GPU.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$ROOT/infra/compose.yaml"
GPU_FILE="$ROOT/infra/compose.gpu.yaml"
REPORTS_DIR="$ROOT/reports"

preset=1
runs=3
timeout=120
target=
augustus=off
cpu=0
no_up=0
no_build=0
no_report=0
dry_run=0
scenarios=

usage() {
  cat <<'EOF'
Usage: run-red-team.sh [options]

Starts dz5, configures P1-P4 guard flags, runs attack-runner and builds reports.

Options:
  --preset N          Protection preset 1-5 (default: 1)
  --runs N            Repetitions per scenario (default: 3; n<3 => unknown)
  --scenario ID       Scenario id to run; may be repeated
  --timeout SECONDS   HTTP timeout (default: 120)
  --target URL        Agent base URL (default: agent for P1-P4, agent-fixed for P5)
  --augustus          Also run Augustus probes
  --cpu               Run ollama on CPU (no NVIDIA GPU required)
  --gpu               Run ollama on GPU (default)
  --no-up             Do not start/update compose services
  --no-build          Do not rebuild the attack-runner image
  --no-report         Skip report_builder
  --dry-run           Print commands without executing them
  -h, --help          Show this help
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --preset)
      [ "$#" -ge 2 ] || fail "missing value for --preset"
      preset=$2
      shift 2
      ;;
    --runs)
      [ "$#" -ge 2 ] || fail "missing value for --runs"
      runs=$2
      shift 2
      ;;
    --scenario)
      [ "$#" -ge 2 ] || fail "missing value for --scenario"
      scenarios="${scenarios}${2}
"
      shift 2
      ;;
    --timeout)
      [ "$#" -ge 2 ] || fail "missing value for --timeout"
      timeout=$2
      shift 2
      ;;
    --target)
      [ "$#" -ge 2 ] || fail "missing value for --target"
      target=$2
      shift 2
      ;;
    --augustus)
      augustus=on
      shift
      ;;
    --cpu)
      cpu=1
      shift
      ;;
    --gpu)
      cpu=0
      shift
      ;;
    --no-up)
      no_up=1
      shift
      ;;
    --no-build)
      no_build=1
      shift
      ;;
    --no-report)
      no_report=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

case "$preset" in
  1|2|3|4|5) ;;
  *) fail "--preset must be 1-5" ;;
esac
case "$runs" in
  ''|*[!0-9]*) fail "--runs must be a positive integer" ;;
esac
[ "$runs" -ge 1 ] || fail "--runs must be at least 1"
[ -n "$timeout" ] || fail "--timeout is required"
if [ "$cpu" -eq 0 ] && [ ! -f "$GPU_FILE" ]; then
  fail "GPU overlay not found: $GPU_FILE"
fi

if [ -z "$target" ]; then
  if [ "$preset" -eq 5 ]; then
    target=http://agent-fixed:8080
  else
    target=http://agent:8080
  fi
fi

builtin_guards=off
external_guard=off
case "$preset" in
  2|4) builtin_guards=on ;;
esac
case "$preset" in
  3|4) external_guard=on ;;
esac
export BUILTIN_GUARDS="$builtin_guards"
export EXTERNAL_GUARD="$external_guard"
export LC_ALL=C

if [ "$dry_run" -eq 0 ]; then
  command -v docker >/dev/null 2>&1 || fail "docker not found in PATH"
  docker compose version >/dev/null 2>&1 || fail "docker compose is not available"
fi

run_cmd() {
  if [ "$dry_run" -eq 1 ]; then
    printf '+'
    for arg in "$@"; do
      printf ' %s' "$arg"
    done
    printf '\n'
  else
    "$@"
  fi
}

compose() {
  if [ "$cpu" -eq 1 ]; then
    run_cmd docker compose -f "$COMPOSE_FILE" --profile attack "$@"
  else
    run_cmd docker compose -f "$COMPOSE_FILE" -f "$GPU_FILE" --profile attack "$@"
  fi
}

list_runs() {
  for entry in "$REPORTS_DIR"/*; do
    [ -d "$entry" ] || continue
    basename "$entry"
  done | sort
}

if [ "$dry_run" -eq 1 ]; then
  if [ "$no_up" -eq 0 ]; then
    compose up -d ollama agent agent-fixed wall ui db-admin
  fi
  set -- run --rm
  if [ "$no_build" -eq 0 ]; then
    set -- "$@" --build
  fi
  set -- "$@" \
    --entrypoint python attack-runner \
    -m attack_runner run \
    --target "$target" \
    --preset "$preset" \
    --runs "$runs" \
    --timeout "$timeout" \
    --augustus "$augustus"
  if [ -n "$scenarios" ]; then
    old_ifs=$IFS
    IFS='
'
    for scenario in $scenarios; do
      set -- "$@" --scenario "$scenario"
    done
    IFS=$old_ifs
  fi
  compose "$@"
  if [ "$no_report" -eq 0 ]; then
    compose run --rm --no-deps --entrypoint python attack-runner \
      -m report_builder build --run /reports/'<run-id>'
  fi
  exit 0
fi

if [ "$no_up" -eq 0 ]; then
  compose up -d ollama agent agent-fixed wall ui db-admin
fi

mkdir -p "$REPORTS_DIR"
before_file=$(mktemp "${TMPDIR:-/tmp}/dz5-runs-before.XXXXXX")
after_file=$(mktemp "${TMPDIR:-/tmp}/dz5-runs-after.XXXXXX")
trap 'rm -f "$before_file" "$after_file"' 0 1 2 15
list_runs >"$before_file"

set -- run --rm
if [ "$no_build" -eq 0 ]; then
  set -- "$@" --build
fi
set -- "$@" \
  --entrypoint python attack-runner \
  -m attack_runner run \
  --target "$target" \
  --preset "$preset" \
  --runs "$runs" \
  --timeout "$timeout" \
  --augustus "$augustus"
if [ -n "$scenarios" ]; then
  old_ifs=$IFS
  IFS='
'
  for scenario in $scenarios; do
    set -- "$@" --scenario "$scenario"
  done
  IFS=$old_ifs
fi
compose "$@"

list_runs >"$after_file"
new_runs=$(comm -13 "$before_file" "$after_file" || true)
run_id=$(printf '%s\n' "$new_runs" | sed '/^$/d' | tail -n 1)
[ -n "$run_id" ] || {
  printf 'error: no new run directory found under reports/\n' >&2
  exit 1
}

if [ "$no_report" -eq 0 ]; then
  compose run --rm --no-deps --entrypoint python attack-runner \
    -m report_builder build --run "/reports/$run_id"
fi

printf '\n'
printf 'run:    %s\n' "$REPORTS_DIR/$run_id"
if [ "$no_report" -eq 1 ]; then
  printf 'report: skipped (--no-report)\n'
else
  printf 'report: %s\n' "$REPORTS_DIR/red-team.md"
fi
if [ "$augustus" = on ]; then
  printf 'augustus: http://localhost:8090/augustus\n'
fi
printf 'ui:       http://localhost:8090/\n'
