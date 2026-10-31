# Builds on the shared base from infra/python: python + uv + unprivileged user.
# Build it once if missing:  ../infra/python/build.ps1  (or build.sh)
#
# One project image serves both fronts: the `ui` extra only adds streamlit, so
# `python -m ai_testgen` (CLI) and `ai-testgen-ui` live in the same image.
# Do not build a second "CLI-only" image — the scripts build this one once.
FROM ohw-python:3.13 AS builder

ARG EXTRAS=""

WORKDIR /app

COPY README.md pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev $EXTRAS

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable $EXTRAS

FROM ohw-python:3.13

COPY --from=builder /app/.venv /app/.venv
COPY prompts /app/prompts
COPY presets /app/presets

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DZ2_PROMPT_DIR=/app/prompts \
    DZ2_PRESETS_DIR=/app/presets

USER app

ENTRYPOINT ["python", "-m", "ai_testgen"]
