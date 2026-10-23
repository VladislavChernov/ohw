FROM golang:1.27 AS augustus

ARG AUGUSTUS_VERSION=0.14.34
RUN CGO_ENABLED=0 go install \
    -ldflags "-X main.version=${AUGUSTUS_VERSION}" \
    github.com/praetorian-inc/augustus/cmd/augustus@v${AUGUSTUS_VERSION}

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/attack-runner \
    DB_PATH=/data/db.sqlite \
    DB_SCRIPTS_DIR=/db \
    REPORTS_DIR=/reports

WORKDIR /app

COPY attack-runner/requirements.txt /app/attack-runner/requirements.txt
RUN pip install --no-cache-dir -r /app/attack-runner/requirements.txt && pip check

COPY --from=augustus /go/bin/augustus /usr/local/bin/augustus
COPY attack-runner /app/attack-runner
COPY report_builder /app/report_builder
COPY scripts/augustus /app/scripts/augustus

RUN augustus version

RUN useradd --create-home --uid 100 runner \
    && mkdir -p /reports /data \
    && chown -R runner:runner /app /reports /data

USER runner

WORKDIR /app/attack-runner

ENTRYPOINT ["python", "-m", "attack_runner"]
CMD ["--help"]
