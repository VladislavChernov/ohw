# Base image (built once via D:\Otus\infra\python\build.ps1):
# python:3.13-slim-bookworm + uv + unprivileged user `app`.
FROM ohw-python:3.13 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY README.md pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM ohw-python:3.13

WORKDIR /data
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app

ENTRYPOINT ["python", "-m", "api_testgen"]
