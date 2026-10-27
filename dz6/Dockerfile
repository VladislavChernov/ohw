FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /data

# Only runtime dependencies of the optimizer and of the generated tests.
COPY requirements/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY prompts ./prompts
COPY input ./input
# The prioritisation stage reads this file; without it the prompt is silently empty.
COPY requirements_with_priorities.md ./requirements_with_priorities.md

ENTRYPOINT ["python", "-m", "src.test_optimizer"]
CMD ["--no-interactive"]
