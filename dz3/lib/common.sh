#!/usr/bin/env bash
# dz3/lib/common.sh — общая логика для start_simple.sh / start_advanced.sh.
# Не запускать напрямую.

set -euo pipefail

# Служебные пути (вычисляются от этого файла, работают из любого cwd).
DZ3_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DZ3_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

GPU_FLAG="${1:-}"
if [[ "$GPU_FLAG" == "--gpu" ]]; then
    GPU_ARGS=(--gpu)
else
    GPU_ARGS=()
fi

# 0) Базовый образ ohw-python:3.13 — нужен app-контейнерам (FROM ohw-python:3.13),
#    нигде не публикуется, собирается локально один раз.
if ! docker image inspect ohw-python:3.13 >/dev/null 2>&1; then
    echo "==> Базовый образ ohw-python:3.13 не найден — собираю..."
    bash "$INFRA_DIR/python/build.sh"
fi

# 1) Общий ollama на сети ohw_net (профиль из dz3/infra.yaml).
echo "==> Поднимаю общий ollama (infra/up.sh)..."
bash "$INFRA_DIR/up.sh" "$DZ3_DIR" "${GPU_ARGS[@]+"${GPU_ARGS[@]}"}"

# 2) Запуск варианта — вызывающий скрипт задаёт SIMPLE_OR_ADVANCED=advanced|simple.
case "$SIMPLE_OR_ADVANCED" in
    simple)   COMPOSE_DIR="$DZ3_DIR/simple" ;;
    advanced) COMPOSE_DIR="$DZ3_DIR" ;;
    *) echo "ОШИБКА: SIMPLE_OR_ADVANCED не задан (advanced|simple)" >&2; exit 1 ;;
esac

echo "==> Запускаю $SIMPLE_OR_ADVANCED ($COMPOSE_DIR)..."
cd "$COMPOSE_DIR"
exec docker compose up --build
