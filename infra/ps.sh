#!/usr/bin/env bash
# infra/ps.sh — status of all shared components in this catalog.
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec docker compose -f "$dir/compose.yaml" ps -a
