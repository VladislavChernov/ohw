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
EXPECTED_DIGEST="${OLLAMA_MODEL_DIGEST#sha256:}"

if ollama ls | awk '{print $1}' | grep -Fxq "$MODEL"; then
  echo "model ready: $MODEL"
else
  echo "pulling '$MODEL' (one-time download)..."
  until ollama pull "$MODEL"; do
    echo "pull failed, retrying in 5s..."
    sleep 5
  done
fi

actual_digest="$(ollama list | awk -v model="$MODEL" '$1 == model {print $2; exit}')"

if [ -z "$actual_digest" ]; then
  echo "model digest not found for '$MODEL'" >&2
  exit 1
fi

if [ -n "$EXPECTED_DIGEST" ]; then
  case "$EXPECTED_DIGEST" in
    "$actual_digest"*)
      echo "model digest verified: $actual_digest"
      ;;
    *)
      echo "model digest mismatch for '$MODEL': expected $EXPECTED_DIGEST, got $actual_digest" >&2
      exit 1
      ;;
  esac
fi

# Keep the server in the foreground.
wait "$server"
