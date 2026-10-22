FROM golang:1.26-alpine AS builder

WORKDIR /src
COPY go.mod ./
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /out/ui .

FROM alpine:3.22

WORKDIR /app
COPY --from=builder /out/ui /app/ui

EXPOSE 8090
ENTRYPOINT ["/app/ui"]
