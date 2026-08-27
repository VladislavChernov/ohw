"""HTTP client for the local ollama service (POST /api/chat)."""

import json

import httpx

DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaConnectionError(Exception):
    """The ollama service is unreachable (network error / connection refused)."""


class OllamaResponseError(Exception):
    """The service answered with an unexpected HTTP status or body."""


class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def chat(self, user: str, system: str = "", temperature: float = 0.7) -> str:
        """Send a chat request and return the model's text reply."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
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