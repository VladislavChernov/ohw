#!/bin/sh
# Serve ollama and ensure the abliterated model is present (pull once),
# then keep the server in the foreground. Mirrors the shared infra entrypoint.
set -e

ollama serve &
server=$!

# Wait until the API answers.
until ollama ls >/dev/null 2>&1; do
  sleep 1
done

MODEL="${OLLAMA_MODEL:-huihui_ai/llama3.2-abliterate:3b}"

if ollama ls | awk '{print $1}' | grep -Fxq "$MODEL"; then
  echo "model ready: $MODEL"
else
  echo "pulling '$MODEL' (one-time download)..."
  until ollama pull "$MODEL"; do
    echo "pull failed, retrying in 5s..."
    sleep 5
  done
fi

# Keep the server in the foreground.
wait "$server"
