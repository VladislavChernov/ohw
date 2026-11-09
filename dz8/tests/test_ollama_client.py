"""Ollama HTTP client: status codes, schemas, timeouts, unknown fields."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from llm_degradation.ollama import (
    ChatResponse,
    LabOllamaClient,
    ModelMissing,
    OllamaResponseError,
    OllamaUnavailable,
    pull_hint,
    start_hint,
)


def client_with(handler: Any, *, model: str = "llama3") -> LabOllamaClient:
    return LabOllamaClient(
        "http://mock:11434",
        model=model,
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


def test_version_and_tags(mock_ollama: Any) -> None:
    mock_ollama.json("GET", "/api/version", {"version": "0.9.1"})
    mock_ollama.json("GET", "/api/tags", {"models": [{"name": "llama3:latest"}]})
    client = mock_ollama.client()
    assert client.version()["version"] == "0.9.1"
    assert client.tags()["models"][0]["name"] == "llama3:latest"


def test_non_200_raises_response_error(mock_ollama: Any) -> None:
    mock_ollama.text("GET", "/api/version", "boom", status=500)
    client = mock_ollama.client()
    with pytest.raises(OllamaResponseError, match="HTTP 500"):
        client.version()


def test_invalid_json_raises_response_error(mock_ollama: Any) -> None:
    mock_ollama.text("GET", "/api/version", "not json")
    client = mock_ollama.client()
    with pytest.raises(OllamaResponseError, match="invalid JSON"):
        client.version()


def test_non_object_json_raises_response_error(mock_ollama: Any) -> None:
    mock_ollama.text("GET", "/api/version", "[1, 2]")
    client = mock_ollama.client()
    with pytest.raises(OllamaResponseError, match="non-object"):
        client.version()


def test_timeout_becomes_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = client_with(handler)
    with pytest.raises(OllamaUnavailable, match="timed out"):
        client.version()


def test_transport_error_becomes_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = client_with(handler)
    with pytest.raises(OllamaUnavailable, match="unreachable"):
        client.version()


def test_show_sends_the_model(mock_ollama: Any) -> None:
    mock_ollama.json("POST", "/api/show", {"modelfile": "FROM llama3"})
    client = mock_ollama.client(model="llama3")
    assert client.show()["modelfile"].startswith("FROM")
    assert client.show("other")["modelfile"].startswith("FROM")


def test_list_models_is_sorted_and_validated(mock_ollama: Any) -> None:
    mock_ollama.json(
        "GET",
        "/api/tags",
        {"models": [{"name": "z"}, {"model": "a"}, "ignored"]},
    )
    client = mock_ollama.client()
    assert client.list_models() == ["a", "z"]


def test_list_models_rejects_non_array(mock_ollama: Any) -> None:
    mock_ollama.json("GET", "/api/tags", {"models": "nope"})
    client = mock_ollama.client()
    with pytest.raises(OllamaResponseError, match="not an array"):
        client.list_models()


def test_find_model_matches_aliases(mock_ollama: Any) -> None:
    tags = {
        "models": [
            {"name": "llama3:latest", "digest": "abc"},
            {"name": "qwen:7b", "digest": "def"},
        ]
    }
    client = mock_ollama.client(model="llama3")
    assert client.find_model(tags=tags)["digest"] == "abc"
    assert client.find_model("llama3:latest", tags)["digest"] == "abc"
    assert client.find_model("qwen:7b", tags)["digest"] == "def"
    assert client.find_model("missing", tags) is None


def test_find_model_handles_bad_tags_payload(mock_ollama: Any) -> None:
    client = mock_ollama.client()
    assert client.find_model(tags={"models": "nope"}) is None


def test_health_ok(mock_ollama: Any) -> None:
    mock_ollama.json("GET", "/api/version", {"version": "0.9.1"})
    mock_ollama.json(
        "GET",
        "/api/tags",
        {"models": [{"name": "llama3:latest", "digest": "abc"}]},
    )
    client = mock_ollama.client()
    result = client.health()
    assert result["ok"] is True
    assert result["digest"] == "abc"
    assert result["ollama_version"] == "0.9.1"
    assert result["command_hint"] is None


def test_health_reports_missing_model(mock_ollama: Any) -> None:
    mock_ollama.json("GET", "/api/version", {"version": "0.9.1"})
    mock_ollama.json("GET", "/api/tags", {"models": [{"name": "other"}]})
    client = mock_ollama.client()
    result = client.health()
    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert result["command_hint"] == pull_hint("llama3")


def test_health_reports_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = client_with(handler)
    result = client.health()
    assert result["ok"] is False
    assert result["command_hint"] == start_hint()
    assert "unreachable" in result["error"]


def test_chat_sends_expected_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "llama3",
                "message": {"content": "hello"},
                "done": True,
                "extra_field": "ignored",
            },
        )

    client = client_with(handler)
    response = client.chat_messages(
        [{"role": "user", "content": "hi"}],
        temperature=0.1,
        seed=7,
        num_ctx=2048,
        num_predict=64,
        json_mode=True,
    )
    assert response == ChatResponse(
        model="llama3",
        content="hello",
        done=True,
        raw={
            "model": "llama3",
            "message": {"content": "hello"},
            "done": True,
            "extra_field": "ignored",
        },
    )
    assert captured["model"] == "llama3"
    assert captured["stream"] is False
    assert captured["format"] == "json"
    assert captured["options"] == {
        "temperature": 0.1,
        "num_ctx": 2048,
        "num_predict": 64,
        "seed": 7,
    }


def test_seed_none_is_omitted() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"model": "llama3", "message": {"content": "ok"}})

    client = client_with(handler)
    client.chat_messages([{"role": "user", "content": "hi"}], seed=None)
    assert "seed" not in captured["options"]
    assert "format" not in captured


def test_chat_helper_returns_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "llama3", "message": {"content": "answer"}})

    client = client_with(handler)
    assert client.chat("system", "user", 0.7) == "answer"


def test_chat_404_is_model_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    client = client_with(handler)
    with pytest.raises(ModelMissing, match="not installed"):
        client.chat("system", "user", 0.7)


def test_chat_other_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="kaboom")

    client = client_with(handler)
    with pytest.raises(OllamaResponseError, match="HTTP 500"):
        client.chat("system", "user", 0.7)


def test_chat_invalid_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = client_with(handler)
    with pytest.raises(OllamaResponseError, match="invalid JSON"):
        client.chat("system", "user", 0.7)


def test_chat_error_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "out of memory"})

    client = client_with(handler)
    with pytest.raises(OllamaResponseError, match="out of memory"):
        client.chat("system", "user", 0.7)


def test_chat_without_message_returns_empty_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "llama3", "done": True})

    client = client_with(handler)
    response = client.chat_messages([{"role": "user", "content": "hi"}])
    assert response.content == ""
    assert response.done is True


def test_timed_chat_returns_latency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "llama3", "message": {"content": "ok"}})

    client = client_with(handler)
    response, latency = client.timed_chat("system", "user")
    assert response.content == "ok"
    assert latency >= 0.0


def test_context_manager_closes_client(mock_ollama: Any) -> None:
    mock_ollama.json("GET", "/api/version", {"version": "1"})
    client = mock_ollama.client()
    with client as entered:
        assert entered is client
    assert client._client.is_closed is True
