"""Ollama API contract checks API-01 ... API-12."""

from __future__ import annotations

import json
from typing import Any

import httpx

from llm_degradation.ollama import LabOllamaClient, OllamaUnavailable
from llm_degradation.probes import api_contract
from llm_degradation.probes.api_contract import (
    UNKNOWN_MODEL,
    ApiCheck,
    api_verdict,
    run_api_checks,
    unavailable_verdict,
)

TAGS_OK = {
    "models": [
        {
            "name": "llama3:latest",
            "model": "llama3:latest",
            "digest": "abc",
            "size": 1,
            "details": {},
        }
    ]
}


def response(status: int = 200, payload: Any = None, text: str | None = None) -> httpx.Response:
    if text is not None:
        return httpx.Response(status, text=text)
    return httpx.Response(status, json=payload)


class FakeClient:
    def __init__(self, result: httpx.Response | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, path, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_version_passes() -> None:
    check = api_contract._version(FakeClient(response(200, {"version": "0.9.1"})))  # type: ignore[arg-type]
    assert check.status == "pass"
    assert check.id == "API-01"


def test_version_fails_without_version() -> None:
    check = api_contract._version(FakeClient(response(200, {})))  # type: ignore[arg-type]
    assert check.status == "fail"
    assert "version is missing" in check.detail


def test_version_reports_unavailable() -> None:
    check = api_contract._version(FakeClient(OllamaUnavailable("down")))  # type: ignore[arg-type]
    assert check.status == "fail"
    assert check.http_status is None


def test_tags_require_all_fields() -> None:
    assert api_contract._tags(FakeClient(response(200, TAGS_OK))).status == "pass"  # type: ignore[arg-type]
    broken = {"models": [{"name": "x"}]}
    assert api_contract._tags(FakeClient(response(200, broken))).status == "fail"  # type: ignore[arg-type]


def test_show_requires_details_and_model_info() -> None:
    payload = {"details": {}, "model_info": {}}
    assert api_contract._show(FakeClient(response(200, payload)), "llama3").status == "pass"  # type: ignore[arg-type]
    with_digest = {"digest": "abc", "details": {}, "model_info": {}}
    assert api_contract._show(FakeClient(response(200, with_digest)), "llama3").status == "pass"  # type: ignore[arg-type]
    assert api_contract._show(FakeClient(response(200, {})), "llama3").status == "fail"  # type: ignore[arg-type]


def test_chat_minimal_requires_content_and_eval_count() -> None:
    payload = {"message": {"content": "OK"}, "done": True, "eval_count": 3}
    assert api_contract._chat_minimal(FakeClient(response(200, payload)), "llama3").status == "pass"  # type: ignore[arg-type]
    empty = {"message": {"content": ""}, "done": True, "eval_count": 0}
    assert api_contract._chat_minimal(FakeClient(response(200, empty)), "llama3").status == "fail"  # type: ignore[arg-type]


def test_chat_json_requires_json_content() -> None:
    payload = {"message": {"content": '{"ok": true}'}}
    assert api_contract._chat_json(FakeClient(response(200, payload)), "llama3").status == "pass"  # type: ignore[arg-type]
    bad = {"message": {"content": "not json"}}
    assert api_contract._chat_json(FakeClient(response(200, bad)), "llama3").status == "fail"  # type: ignore[arg-type]


def test_unknown_model_requires_404() -> None:
    ok = api_contract._chat_unknown_model(
        FakeClient(response(404, {"error": "not found"})), UNKNOWN_MODEL
    )
    assert ok.status == "pass"
    wrong = api_contract._chat_unknown_model(
        FakeClient(response(200, {"message": {"content": "hi"}})), UNKNOWN_MODEL
    )
    assert wrong.status == "fail"


def test_malformed_json_requires_400() -> None:
    assert api_contract._chat_malformed_json(FakeClient(response(400, text="bad"))).status == "pass"
    assert api_contract._chat_malformed_json(FakeClient(response(200, {}))).status == "fail"


def test_options_check_warns_on_large_eval_count() -> None:
    payload = {"prompt_eval_count": 5, "eval_count": 10}
    assert api_contract._chat_options(FakeClient(response(200, payload)), "llama3").status == "pass"  # type: ignore[arg-type]
    warning = {"prompt_eval_count": 5, "eval_count": 100}
    assert api_contract._chat_options(FakeClient(response(200, warning)), "llama3").status == "warn"  # type: ignore[arg-type]
    failure = {"prompt_eval_count": 0, "eval_count": 100}
    assert api_contract._chat_options(FakeClient(response(200, failure)), "llama3").status == "fail"  # type: ignore[arg-type]


def test_stream_check_is_optional() -> None:
    ok = api_contract._chat_stream(
        FakeClient(response(200, text='{"done": true}\n{"done": true}')), "llama3"
    )
    assert ok.status == "pass"
    assert ok.critical is False
    bad = api_contract._chat_stream(FakeClient(response(200, text="not json")), "llama3")
    assert bad.status == "warn"
    assert bad.critical is False


def test_timeout_check_passes_when_request_times_out() -> None:
    client = FakeClient(OllamaUnavailable("timed out"))
    check = api_contract._timeout(client, "llama3")  # type: ignore[arg-type]
    assert check.status == "pass"
    assert client.calls[0][2]["timeout"] == 0.001


def test_timeout_check_fails_when_request_returns() -> None:
    check = api_contract._timeout(FakeClient(response(200, {})), "llama3")  # type: ignore[arg-type]
    assert check.status == "fail"


def test_ps_accepts_404() -> None:
    check = api_contract._ps(FakeClient(response(404, {"error": "not found"})))  # type: ignore[arg-type]
    assert check.status == "pass"
    assert check.critical is False


def test_parallel_check_uses_three_calls() -> None:
    client = FakeClient(response(200, {"message": {"content": "OK"}}))
    check = api_contract._parallel(client, "llama3")  # type: ignore[arg-type]
    assert check.status == "pass"
    assert len(client.calls) == 3


def test_api_verdict_pass_warn_fail() -> None:
    pass_check = ApiCheck(id="API-01", name="x", status="pass")
    fail_check = ApiCheck(id="API-02", name="y", status="fail")
    warn_check = ApiCheck(id="API-09", name="z", status="warn", critical=False)
    assert api_verdict([pass_check]).status == "pass"
    assert api_verdict([pass_check, warn_check]).status == "warn"
    verdict = api_verdict([pass_check, fail_check])
    assert verdict.status == "fail"
    assert "API-02" in verdict.reasons[0]


def test_non_critical_failure_does_not_fail_the_contract() -> None:
    optional = ApiCheck(id="API-12", name="z", status="fail", critical=False)
    assert api_verdict([optional]).status == "pass"


def test_unavailable_verdict() -> None:
    verdict = unavailable_verdict("ollama is down")
    assert verdict.status == "fail"
    assert verdict.reasons == ["ollama is down"]


def contract_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/version":
        return response(200, {"version": "0.9.1"})
    if path == "/api/tags":
        return response(200, TAGS_OK)
    if path == "/api/show":
        return response(200, {"digest": "abc", "details": {}, "model_info": {}})
    if path == "/api/ps":
        return response(200, {"models": []})
    if path == "/api/chat":
        try:
            body = json.loads(request.content)
        except json.JSONDecodeError:
            return response(400, {"error": "bad request"})
        if body.get("model") == UNKNOWN_MODEL:
            return response(404, {"error": "model not found"})
        if "options" not in body:
            raise httpx.ReadTimeout("slow", request=request)
        if body.get("stream"):
            return response(200, text='{"done": true}\n')
        content = '{"ok": true}' if body.get("format") == "json" else "OK"
        return response(
            200,
            {
                "model": body.get("model"),
                "message": {"content": content},
                "done": True,
                "eval_count": 3,
                "prompt_eval_count": 5,
            },
        )
    return response(404, {"error": "unknown path"})


def test_run_api_checks_all_pass() -> None:
    client = LabOllamaClient(
        "http://mock:11434",
        model="llama3",
        timeout=1.0,
        transport=httpx.MockTransport(contract_handler),
    )
    checks = run_api_checks(client, "llama3")
    assert [item.id for item in checks] == [f"API-{index:02d}" for index in range(1, 13)]
    failed = [item.id for item in checks if item.status == "fail"]
    assert failed == []
    assert api_verdict(checks).status == "pass"


def test_run_api_checks_reports_progress() -> None:
    client = LabOllamaClient(
        "http://mock:11434",
        model="llama3",
        timeout=1.0,
        transport=httpx.MockTransport(contract_handler),
    )
    seen: list[tuple[int, int, str]] = []
    checks = run_api_checks(
        client,
        "llama3",
        progress=lambda index, total, check: seen.append((index, total, check.id)),
    )
    assert len(seen) == 12
    assert seen[0][:2] == (1, 12)
    assert seen[-1] == (12, 12, "API-12")
    assert checks[-1].id == "API-12"
