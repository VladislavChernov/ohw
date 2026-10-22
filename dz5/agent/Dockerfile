FROM golang:1.26-alpine AS builder

ARG TARGET_PKG=.

WORKDIR /src

COPY go.mod ./
RUN go mod download modernc.org/sqlite

COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -trimpath -o /out/agent ${TARGET_PKG}

FROM alpine:3.22

WORKDIR /app

COPY prompts /app/prompts
COPY fixed/prompts /app/fixed-prompts

COPY --from=builder /out/agent /app/agent

EXPOSE 8080
ENTRYPOINT ["/app/agent"]
