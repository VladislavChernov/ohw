#!/usr/bin/env sh
# Seed or restore the dz5 SQLite volume.
#
#   sh seed-db.sh              # initial schema + synthetic seed data
#   sh seed-db.sh --restore    # restore after destructive LLM03 tests
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$ROOT/infra/compose.yaml"

mode=seed
case "${1:-}" in
  ""|--seed)
    mode=seed
    ;;
  --restore)
    mode=restore
    ;;
  -h|--help)
    cat <<'EOF'
Usage: seed-db.sh [--seed|--restore]

  --seed      Create the schema and load synthetic users/PII (default)
  --restore   Restore the DB to the same seed state
EOF
    exit 0
    ;;
  *)
    printf 'error: unknown argument: %s\n' "$1" >&2
    exit 2
    ;;
esac

if [ "$#" -gt 1 ]; then
  printf 'error: too many arguments\n' >&2
  exit 2
fi

command -v docker >/dev/null 2>&1 || {
  printf 'error: docker not found in PATH\n' >&2
  exit 127
}
docker compose version >/dev/null 2>&1 || {
  printf 'error: docker compose is not available\n' >&2
  exit 127
}

docker compose -f "$COMPOSE_FILE" --profile tools run --rm "db-$mode"
