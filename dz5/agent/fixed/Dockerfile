FROM golang:1.26-alpine AS builder
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /app/agent-fixed .

FROM scratch
COPY --from=builder /app/agent-fixed /app/agent-fixed
COPY --from=builder /src/prompts /app/prompts
EXPOSE 8080
ENV AGENT_ADDR=:8080
ENV BUILTIN_GUARDS=on
ENV EXTERNAL_GUARD=off
ENV ALLOW_WRITES=off
ENV SCAN_INPUT=on
ENV SCAN_OUTPUT=on
ENV BLOCK_ON_FAIL=on
ENTRYPOINT ["/app/agent-fixed"]