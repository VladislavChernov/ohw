FROM ohw-python:3.13

# The shared image already contains Python 3.13 and uv. The project is not
# installed as a wheel; it is executed from /app with PYTHONPATH=/app.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY input ./input
COPY scripts ./scripts

ENTRYPOINT ["uv", "run", "--no-dev", "python", "-m", "src.workflow.main"]
