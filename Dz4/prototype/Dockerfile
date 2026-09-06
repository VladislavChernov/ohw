# GraphRAG proto service image (Config/Glossary), M0.
FROM python:3.13-slim-bookworm

RUN pip install --no-cache-dir uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --no-dev --frozen

ENV DOMAIN_PROFILES_DIR=/app/domain_profiles \
    CONFIG_DB_PATH=/app/runtime/config.db

ENTRYPOINT ["uv", "run", "--no-sync"]
CMD ["graphrag-config"]