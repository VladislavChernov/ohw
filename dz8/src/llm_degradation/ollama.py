"""Ollama HTTP client used by the lab.

The client is deliberately explicit about transport errors versus HTTP status
codes: contract checks need the status of ``/api/chat`` for an unknown model,
while the rest of the lab wants a typed error. ``request`` therefore returns
the raw response, and the typed helpers raise.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_BASE_URL = "http://ohw-ollama:11434"
DEFAULT_MODEL = "llama3"
DEFAULT_TIMEOUT = 600.0


class OllamaUnavailable(Exception):
    """Ollama is unreachable or timed out (exit code 3)."""


class ModelMissing(OllamaUnavailable):
    """The requested model is not installed (exit code 3)."""


class OllamaResponseError(Exception):
    """Ollama returned an unexpected HTTP response (exit code 4)."""


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    content: str
    done: bool = False
    done_reason: str | None = None
    eval_count: int = 0
    prompt_eval_count: int = 0
    total_duration: int = 0
    load_duration: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)


class LabOllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LabOllamaClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- raw requests ------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        content: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        try:
            return self._client.request(
                method,
                path,
                json=json_body,
                content=content,
                headers=headers,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable(
                f"Ollama at {self.base_url} timed out after "
                f"{timeout if timeout is not None else self.timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Ollama at {self.base_url} is unreachable: {exc}") from exc

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.request(method, path, **kwargs)
        if response.status_code != 200:
            raise OllamaResponseError(
                f"{method} {path} returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaResponseError(f"{path} returned invalid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise OllamaResponseError(f"{path} returned a non-object JSON payload")
        return body

    # -- API endpoints -----------------------------------------------------

    def version(self) -> dict[str, Any]:
        return self._json("GET", "/api/version")

    def tags(self) -> dict[str, Any]:
        return self._json("GET", "/api/tags")

    def show(self, model: str | None = None) -> dict[str, Any]:
        return self._json("POST", "/api/show", json_body={"model": model or self.model})

    def ps(self) -> dict[str, Any]:
        return self._json("GET", "/api/ps")

    def list_models(self) -> list[str]:
        body = self.tags()
        models = body.get("models", [])
        if not isinstance(models, list):
            raise OllamaResponseError("/api/tags: 'models' is not an array")
        return sorted(
            str(item.get("name") or item.get("model") or "")
            for item in models
            if isinstance(item, dict)
        )

    def find_model(
        self, model: str | None = None, tags: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        wanted = model or self.model
        body = tags if tags is not None else self.tags()
        models = body.get("models", [])
        if not isinstance(models, list):
            return None
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "")
            base = name.split(":", 1)[0]
            if name == wanted or base == wanted or f"{wanted}:latest" == name:
                return item
        return None

    def health(self, model: str | None = None) -> dict[str, Any]:
        """Version, tags and model presence. Never starts a chat."""
        wanted = model or self.model
        try:
            version_body = self.version()
            tags_body = self.tags()
        except OllamaUnavailable as exc:
            return {
                "ok": False,
                "base_url": self.base_url,
                "model": wanted,
                "error": str(exc),
                "command_hint": start_hint(),
            }
        found = self.find_model(wanted, tags_body)
        names = sorted(
            str(item.get("name") or item.get("model") or "")
            for item in tags_body.get("models", [])
            if isinstance(item, dict)
        )
        result = {
            "ok": found is not None,
            "base_url": self.base_url,
            "ollama_version": str(version_body.get("version", "")),
            "model": wanted,
            "digest": str(found.get("digest", "")) if found else None,
            "models": names,
            "command_hint": None if found else pull_hint(wanted),
        }
        if found is None:
            result["error"] = f"model {wanted!r} is not installed"
        return result

    # -- chat --------------------------------------------------------------

    def chat_messages(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        seed: int | None = 42,
        num_ctx: int = 4096,
        num_predict: int = 512,
        json_mode: bool = False,
        model: str | None = None,
        stream: bool = False,
        timeout: float | None = None,
    ) -> ChatResponse:
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        }
        if seed is not None:
            options["seed"] = seed
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"

        response = self.request("POST", "/api/chat", json_body=payload, timeout=timeout)
        if response.status_code == 404:
            raise ModelMissing(
                f"model {payload['model']!r} is not installed: {response.text[:200]}"
            )
        if response.status_code != 200:
            raise OllamaResponseError(
                f"POST /api/chat returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaResponseError(f"POST /api/chat returned invalid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise OllamaResponseError("POST /api/chat returned a non-object JSON payload")
        if body.get("error"):
            raise OllamaResponseError(f"Ollama error: {body['error']}")
        message = body.get("message")
        content = message.get("content", "") if isinstance(message, dict) else ""
        return ChatResponse(
            model=str(body.get("model", payload["model"])),
            content=str(content or ""),
            done=bool(body.get("done", False)),
            done_reason=body.get("done_reason"),
            eval_count=int(body.get("eval_count", 0) or 0),
            prompt_eval_count=int(body.get("prompt_eval_count", 0) or 0),
            total_duration=int(body.get("total_duration", 0) or 0),
            load_duration=int(body.get("load_duration", 0) or 0),
            raw=body,
        )

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = False,
        model: str | None = None,
        seed: int | None = 42,
        num_ctx: int = 4096,
        num_predict: int = 512,
    ) -> str:
        """Dz2-compatible signature used by ``ai_testgen.service.generate_artifact``."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response = self.chat_messages(
            messages,
            temperature=temperature,
            seed=seed,
            num_ctx=num_ctx,
            num_predict=num_predict,
            json_mode=json_mode,
            model=model,
        )
        return response.content

    def timed_chat(self, system: str, user: str, **kwargs: Any) -> tuple[ChatResponse, float]:
        started = time.perf_counter()
        response = self.chat_messages(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        return response, (time.perf_counter() - started) * 1000.0


def start_hint() -> str:
    return (
        "Start the stand: cd dz2 && .\\scripts\\up-cpu.ps1 (or up-gpu.ps1), then check: docker ps"
    )


def pull_hint(model: str) -> str:
    return f"docker exec ohw-ollama ollama pull {model}"
