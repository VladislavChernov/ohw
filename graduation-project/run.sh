#!/usr/bin/env sh
# Runs the graduation project against the shared infra containers
# (ohw-ollama + ohw-python:3.13, network ohw_net).
set -eu
cd "$(dirname "$0")"

if [ ! -f ../infra/compose.yaml ]; then
    echo "error: ../infra not found; this project uses the shared ohw infra" >&2
    exit 1
fi

if ! docker inspect --format '{{.State.Running}}' ohw-ollama 2>/dev/null | grep -q true; then
    echo "starting shared infra (profile ollama)..."
    docker compose -f ../infra/compose.yaml --profile ollama up -d
fi

i=0
while [ "$i" -lt 90 ]; do
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ohw-ollama 2>/dev/null || echo missing)
    if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then break; fi
    i=$((i + 1))
    sleep 2
done
if [ "$i" -ge 90 ]; then
    echo "warning: ohw-ollama is not healthy after 180s; continuing" >&2
fi

if [ "$#" -gt 0 ]; then
    exec docker compose run --rm --build app "$@"
fi

exec docker compose up --build --abort-on-container-exit \
    --exit-code-from app
