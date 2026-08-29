"""HTTP client for a local Ollama service (POST /api/chat)."""

from __future__ import annotations

import json

import httpx

DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaConnectionError(Exception):
    """The Ollama service is unreachable (network error / connection refused)."""


class OllamaResponseError(Exception):
    """The service answered with an unexpected HTTP status or body."""


class OllamaClient:
    """Minimal synchronous client for ``/api/chat``.

    ``transport`` lets tests inject ``httpx.MockTransport`` without a live
    service. ``json_mode`` adds ``"format": "json"`` to the payload so the
    model returns strict JSON (Ollama's constrained decoding).
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        timeout: float = 300.0,
        json_mode: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.json_mode = json_mode
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def chat(self, system: str = "", user: str = "", temperature: float = 0.7) -> str:
        """Send one turn and return the model's text reply.

        Use keyword arguments: ``chat(system=..., user=...)`` mirrors the
        server message shape (system first, user last).
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        if not user:
            raise ValueError("chat() requires a non-empty 'user' message")
        messages.append({"role": "user", "content": user})
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if self.json_mode:
            payload["format"] = "json"
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                f"ollama service at {self.base_url} is unreachable: {exc}"
            ) from exc
        if response.status_code != 200:
            raise OllamaResponseError(
                f"ollama returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            body = response.json()
            return str(body["message"]["content"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OllamaResponseError(f"unexpected ollama response structure: {exc}") from exc