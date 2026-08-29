"""Tests for ohw_kit.ollama_client."""

from __future__ import annotations

import json

import httpx
import pytest

from ohw_kit.ollama_client import (
    DEFAULT_BASE_URL,
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)


def _client(handler: object) -> OllamaClient:
    return OllamaClient(
        base_url="http://mock",
        model="m",
        timeout=5.0,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def test_default_base_url() -> None:
    assert DEFAULT_BASE_URL == "http://localhost:11434"


def test_chat_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["model"] == "m"
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "user"
        assert "format" not in payload
        return httpx.Response(200, json={"message": {"content": "hello"}})

    reply = _client(handler).chat(user="hi")
    assert reply == "hello"


def test_chat_includes_system_first() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read()))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    _client(handler).chat(system="be terse", user="hi")
    assert seen[0]["messages"][0] == {"role": "system", "content": "be terse"}
    assert seen[0]["messages"][1] == {"role": "user", "content": "hi"}


def test_json_mode_adds_format() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.read()))
        return httpx.Response(200, json={"message": {"content": "{}"}})

    client = _client(handler)
    client.json_mode = True
    client.chat(user="hi")
    assert seen[0]["format"] == "json"


def test_empty_user_raises() -> None:
    client = _client(lambda request: httpx.Response(200, json={"message": {"content": ""}}))
    with pytest.raises(ValueError):
        client.chat(user="")


def test_http_error() -> None:
    client = _client(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(OllamaResponseError):
        client.chat(user="hi")


def test_broken_json() -> None:
    client = _client(lambda request: httpx.Response(200, text="not-json"))
    with pytest.raises(OllamaResponseError):
        client.chat(user="hi")


def test_missing_content_key() -> None:
    client = _client(lambda request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(OllamaResponseError):
        client.chat(user="hi")


def test_connection_error() -> None:
    client = OllamaClient(base_url="http://127.0.0.1:1", model="m", timeout=0.05)
    with pytest.raises(OllamaConnectionError):
        client.chat(user="hi")