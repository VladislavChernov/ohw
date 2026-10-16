# Go agent for the dz5 security stand (preset 1 / Baseline).
#
# Multi-stage: golang toolchain builds a static binary (CGO off, modernc
# sqlite is pure Go), then a minimal alpine runtime ships only the binary and
# the system prompts. All tags are pinned (not :latest) for reproducibility.
FROM golang:1.26-alpine AS builder

WORKDIR /src

# Module metadata first so dependency download is a separate cached layer.
COPY go.mod ./
RUN go mod download modernc.org/sqlite

COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -trimpath -o /out/agent .

FROM alpine:3.22

WORKDIR /app

# System prompts for the BUILTIN_GUARDS switch (minimal.txt / guarded.txt).
COPY prompts /app/prompts

COPY --from=builder /out/agent /app/agent

EXPOSE 8080
ENTRYPOINT ["/app/agent"]
