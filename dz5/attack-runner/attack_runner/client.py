from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_BODY_BYTES = 64 * 1024
LOCAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "agent",
    "agent-fixed",
    "host.docker.internal",
}


class TargetError(ValueError):
    pass


@dataclass(frozen=True)
class StepResponse:
    status_code: int | None
    latency_ms: int | None
    body: dict[str, Any] | None
    text: str
    error: str | None = None

    @property
    def reply(self) -> str:
        if isinstance(self.body, dict):
            value = self.body.get("reply")
            if isinstance(value, str):
                return value
        return ""

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        if isinstance(self.body, dict):
            value = self.body.get("tool_calls")
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []


def validate_target(target: str) -> str:
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TargetError(f"target must be an http(s) URL, got: {target}")
    if parsed.hostname not in LOCAL_HOSTS:
        raise TargetError(
            f"target host must be local ({', '.join(sorted(LOCAL_HOSTS))}), "
            f"got: {parsed.hostname}"
        )
    return target.rstrip("/")


class AgentClient:
    def __init__(self, target: str, timeout: float = 60.0) -> None:
        self.target = validate_target(target)
        self.timeout = timeout

    def chat(self, message: str) -> StepResponse:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                response = client.post(
                    f"{self.target}/chat",
                    json={"message": message},
                    headers={"Content-Type": "application/json"},
                )
            latency_ms = round((time.perf_counter() - started) * 1000)
            text = response.content[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
            body: dict[str, Any] | None = None
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    body = parsed
            except json.JSONDecodeError:
                parsed = None
            error = None
            if response.status_code == 200 and body is None:
                error = "response is not a JSON object"
            return StepResponse(
                status_code=response.status_code,
                latency_ms=latency_ms,
                body=body,
                text=text,
                error=error,
            )
        except (httpx.TimeoutException, httpx.HTTPError, OSError) as exc:
            latency_ms = round((time.perf_counter() - started) * 1000)
            return StepResponse(
                status_code=None,
                latency_ms=latency_ms,
                body=None,
                text="",
                error=f"{type(exc).__name__}: {exc}",
            )
