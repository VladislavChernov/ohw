#!/usr/bin/env bash
# dz3/start_simple.sh — запустить вариант SIMPLE в Docker (E2E).
#   ./start_simple.sh           # CPU-инференс
#   ./start_simple.sh --gpu     # NVIDIA CUDA для общего ollama
# Скрипт сам: соберёт base-образ (если нет), поднимет общий ollama (infra/up.sh)
# и запустит dz3/simple/compose.yaml. Отчёт — dz3/simple/output/.

set -euo pipefail
export SIMPLE_OR_ADVANCED=simple
exec bash "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh" "$@"
