# Local abliterated LLM target for the dz5 security stand (GPU).
#
# Deliberately NOT the shared ohw-ollama: the abliterated model has its safety
# alignment stripped on purpose, so a vulnerable baseline can be demonstrated.
# Keeping it in a dedicated image/volume avoids polluting the shared model
# volume that other homeworks use.
#
# Engine image is pinned (not :latest) so every machine runs the same
# inference engine. 0.40.4 verified locally on Docker Desktop with NVIDIA GPU.
FROM ollama/ollama:0.40.4

# Target (victim) model: small abliterated instruct, safety alignment removed.
# Overridable via OLLAMA_MODEL at compose time.
ENV OLLAMA_MODEL=huihui_ai/llama3.2-abliterate:3b

# Entrypoint that serves ollama and pulls the model once, then waits.
# Mirrors the shared infra entrypoint contract (healthcheck-friendly).
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]

