# Local abliterated LLM inference for the dz5 security stand.
#
# Deliberately NOT the shared ohw-ollama: the abliterated model has its safety
# alignment stripped on purpose, so a vulnerable baseline can be demonstrated.
# Keeping it in a dedicated image/volume avoids polluting the shared model
# volume that other homeworks use.
#
# The engine image is pinned exactly like the shared infra (not :latest) so
# every machine runs the same inference engine.
FROM ollama/ollama:0.33.3

# Model to pull on first start (abliterated instruct, safety alignment removed).
# Overridable via OLLAMA_MODEL at compose time.
ENV OLLAMA_MODEL=qwen2.5:1.5b-instruct-q8_0

# Entrypoint that serves ollama and pulls the model once, then waits.
# Mirrors the shared infra entrypoint contract (healthcheck-friendly).
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]
