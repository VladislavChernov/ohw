# ohw-python:3.13 — shared base image for homework app containers.
# Built once from this folder (build.sh); projects inherit FROM it instead of
# repeating python + uv + user app in every Dockerfile.
# Components are NOT bundled: build tools stay in pip layer, no git/curl extras.
# Python version is pinned exactly (was 3.13.5 in ohw-python:3.13 / devcontainer);
# bump consciously and rerun unit tests + E2E after a bump.
FROM python:3.13.5-slim-bookworm

RUN pip install --no-cache-dir uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Unprivileged runtime user, reused by project images (USER app).
RUN groupadd -r app && useradd -r -g app app

WORKDIR /data
