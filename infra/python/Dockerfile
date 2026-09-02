# ohw-python:3.13 — shared base image for homework app containers.
# Built once from this folder (build.ps1); projects inherit FROM it instead of
# repeating python:3.13-slim-bookworm + pip install uv + user app in every Dockerfile.
# Components are NOT bundled: build tools stay in pip layer, no git/curl extras.
FROM python:3.13-slim-bookworm

RUN pip install --no-cache-dir uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Unprivileged runtime user, reused by project images (USER app).
RUN groupadd -r app && useradd -r -g app app

WORKDIR /data
