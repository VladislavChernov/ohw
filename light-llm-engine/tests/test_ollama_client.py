"""Unit tests for the llm_engine package."""

import json

import httpx
import pytest

from llm_engine.ollama_client import (
    DEFAULT_BASE_URL,
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)


def _client(handler) -> OllamaClient:
    return OllamaClient(
        base_url="http://mock",
        model="m",
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


def test_default_base_url() -> None:
    assert DEFAULT_BASE_URL == "http://localhost:11434"


def test_chat_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["model"] == "m"
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(200, json={"message": {"content": "hello"}})

    reply = _client(handler).chat("hi")
    assert reply == "hello"


def test_chat_includes_system() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read()))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    _client(handler).chat("hi", system="be terse")
    assert seen[0]["messages"][0] == {"role": "system", "content": "be terse"}
    assert seen[0]["messages"][1] == {"role": "user", "content": "hi"}


def test_http_error() -> None:
    client = _client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(OllamaResponseError):
        client.chat("hi")


def test_broken_json() -> None:
    client = _client(lambda request: httpx.Response(200, text="not-json"))
    with pytest.raises(OllamaResponseError):
        client.chat("hi")


def test_missing_content_key() -> None:
    client = _client(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(OllamaResponseError):
        client.chat("hi")


def test_connection_error() -> None:
    client = OllamaClient(base_url="http://127.0.0.1:1", model="m", timeout=0.05)
    with pytest.raises(OllamaConnectionError):
        client.chat("hi")