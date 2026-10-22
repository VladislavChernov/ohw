FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip check

# Bake NLTK/presidio assets so the wall does not download models per request.
RUN python -m guardrails_ai.detect_pii.post_install

COPY config.py .

# Guardrails sends Hub telemetry to an AWS OTLP endpoint by default. In this
# offline lab that exporter blocks requests with DNS retries, so disable it.
RUN printf 'enable_metrics=false\n' > /root/.guardrailsrc
ENV OTEL_SDK_DISABLED=true
ENV OTEL_TRACES_EXPORTER=none

ENV GR_CONFIG_FILE_PATH=/app/config.py
ENV GUARDRAILS_LOG_LEVEL=INFO

EXPOSE 8000

# `guardrails-api start` calls uvicorn.run(app, port=port) without host, i.e.
# binds 127.0.0.1 and is unreachable from other containers. The factory entry
# point is the supported way to bind 0.0.0.0.
CMD ["uvicorn", "--factory", "guardrails_api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000"]
