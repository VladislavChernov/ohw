#!/usr/bin/env bash
# dz3/start_advanced.sh — запустить вариант ADVANCED в Docker (E2E).
#   ./start_advanced.sh           # CPU-инференс
#   ./start_advanced.sh --gpu     # NVIDIA CUDA для общего ollama
# Скрипт сам: соберёт base-образ (если нет), поднимет общий ollama (infra/up.sh)
# и запустит dz3/compose.yaml. Отчёт — dz3/advanced/output/report.md, план — plan.json.

set -euo pipefail
export SIMPLE_OR_ADVANCED=advanced
exec bash "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh" "$@"
