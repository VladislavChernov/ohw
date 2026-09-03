#!/usr/bin/env bash
# infra/down.sh — stop all shared components (everything in this catalog).
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Stopping shared infrastructure..."
exec docker compose -f "$dir/compose.yaml" down
