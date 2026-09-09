from __future__ import annotations

import pytest
import requests

from graphrag_proto.demo_ui.client import (
    DemoClient,
    Settings,
    _raise_for_status,
    parse_sse,
)


def _response(status_code: int, body: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    return response


def test_parse_sse_event_pairs() -> None:
    lines = [
        "event: status",
        'data: {"stage": "retrieve"}',
        "",
        "event: token",
        'data: {"text": "привет"}',
        "",
        'event: done',
        'data: {"text": "итог", "sources": [], "generation_time_s": 0.5}',
        "",
    ]
    events = list(parse_sse(lines))
    assert events[0] == ("status", {"stage": "retrieve"})
    assert events[1] == ("token", {"text": "привет"})
    assert events[2][0] == "done"
    assert events[2][1]["text"] == "итог"
    assert events[2][1]["generation_time_s"] == 0.5


def test_parse_sse_comment_and_default_event_type() -> None:
    events = list(parse_sse([": keepalive", "data: {}", ""]))
    assert events == [("message", {})]


def test_parse_sse_keepalive_comments_only() -> None:
    assert list(parse_sse([": keepalive", ": ping", ""])) == []


def test_parse_sse_unknown_event_type_passthrough() -> None:
    events = list(parse_sse(["event: custom", 'data: {"a": 1}', ""]))
    assert events == [("custom", {"a": 1})]


def test_raise_for_status_error_detail() -> None:
    response = _response(422, '{"detail": "doc_type=\'bin\' не поддерживается"}')
    with pytest.raises(RuntimeError, match="422"):
        _raise_for_status(response)


def test_upload_builds_expected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _response(202, '{"job_id": "j1", "status": "queued"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(ingestion_url="http://ing:8002", api_key="k123"))
    body = client.upload_document("текст документа", "txt", "doc", "s://demo/a.txt")
    assert body["job_id"] == "j1"
    assert captured["url"] == "http://ing:8002/api/v1/ingestion/documents"
    assert captured["headers"] == {"X-API-Key": "k123"}
    assert captured["json"] == {
        "source_url": "s://demo/a.txt",
        "domain": "doc",
        "doc_type": "txt",
        "content": "текст документа",
    }


def test_submit_query_requires_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        return _response(202, '{"status": "accepted"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(query_url="http://query:8000", api_key="k"))
    with pytest.raises(RuntimeError, match="task_id"):
        client.submit_query("вопрос", "doc")


def test_submit_query_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        captured["url"] = url
        captured["json"] = json
        return _response(202, '{"task_id": "t1"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(query_url="http://query:8000", api_key="k"))
    assert client.submit_query("вопрос про домен", "doc") == "t1"
    assert captured["url"] == "http://query:8000/query"
    assert captured["json"] == {"query": "вопрос про домен", "metadata": {"domain": "doc"}}