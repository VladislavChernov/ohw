import json

import httpx

DEFAULT_BASE_URL = "http://host.docker.internal:11434"


class OllamaConnectionError(Exception):
    pass


class OllamaResponseError(Exception):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        timeout: float = 180.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def chat(self, system: str, user: str, temperature: float, *, json_mode: bool = True) -> str:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(f"ollama service at {self.base_url} is unreachable: {exc}") from exc
        if response.status_code != 200:
            raise OllamaResponseError(
                f"ollama returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            body = response.json()
            return str(body["message"]["content"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OllamaResponseError(f"unexpected ollama response structure: {exc}") from exc
