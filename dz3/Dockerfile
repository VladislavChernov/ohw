# Общий Dockerfile Dz3 (advanced). Контекст сборки — КОРЕНЬ МОНОРЕПО (ohw/),
# потому что advanced зависит от `ohw-kit @ file:../../kit` (kit лежит в ohw/kit),
# и `uv sync` резолвит этот относительный путь из файловой системы build-контекста.
#   docker compose -f dz3/compose.yaml up --build
FROM ohw-python:3.13 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/dz3/advanced

# ohw-kit @ file:../../kit -> /app/kit (relative to this WORKDIR); copy it so uv can resolve it.
COPY kit /app/kit
COPY dz3/advanced/README.md dz3/advanced/pyproject.toml dz3/advanced/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev

COPY dz3/advanced/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM ohw-python:3.13

WORKDIR /data
COPY --from=builder /app/dz3/advanced/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app

ENTRYPOINT ["python", "-m"]
