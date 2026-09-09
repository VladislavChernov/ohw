#!/usr/bin/env bash
# add-demo-ui-e2e: сброс стека -> подъём -> pytest -m e2e.
#
# Predусловие детерминизма e2e — ЧИСТЫЕ volume'ы (top_k=5 + хэш-эмбеддинги:
# чужой мусор в графе вытесняет загруженный документ из done.sources).
# По умолчанию: стек сбрасывается и после прогона остаётся поднятым.
#   --keep-volumes — не удалять volume'ы      --down — погасить стек _и_ снять volume'ы
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROTO="$ROOT/prototype"
PROFILES=(--profile config --profile graph --profile ingestion --profile llm)

RESET_VOLUMES=1
TEARDOWN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-volumes) RESET_VOLUMES=0 ;;
    --down) TEARDOWN=1 ;;
    *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$PROTO"

if [[ "$RESET_VOLUMES" -eq 1 ]]; then
  echo "[demo-e2e] сброс стека (down -v)…"
  docker compose "${PROFILES[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
fi

echo "[demo-e2e] подъём стека: config+graph+ingestion+llm (--wait)…"
docker compose "${PROFILES[@]}" up -d --wait

KEY="${GRAPH_AUTH_API_KEY:-changeme}"

if command -v uv >/dev/null 2>&1; then
  echo "[demo-e2e] прогон pytest -m e2e (uv на хосте)…"
  set +e
  uv run pytest -m e2e \
    --e2e-ingest=http://localhost:8002 \
    --e2e-query=http://localhost:8000 \
    --e2e-key="$KEY" -v
  rc=$?
  set -e
else
  echo "[demo-e2e] прогон pytest -m e2e (через ohw-python контейнер)…"
  set +e
  docker run --rm -v "$PROTO:/app" -w /app ohw-python:3.13 bash -lc \
    "uv run pytest -m e2e --e2e-ingest=http://host.docker.internal:8002 --e2e-query=http://host.docker.internal:8000 --e2e-key=\"$KEY\" -v"
  rc=$?
  set -e
fi

if [[ "$TEARDOWN" -eq 1 ]]; then
  echo "[demo-e2e] гашение стека (down -v)…"
  docker compose "${PROFILES[@]}" down -v --remove-orphans
fi

exit "$rc"