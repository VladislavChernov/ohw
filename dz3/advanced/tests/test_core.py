"""Tests for the HTTP core engine (no live network; MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from json_testgen_advanced.core import execute_plan
from json_testgen_advanced.plan import TestPlan


def _plan(doc: dict) -> TestPlan:
    return TestPlan.from_dict(doc)


def _run(doc: dict, handler: Callable[[httpx.Request], httpx.Response]):
    transport = httpx.MockTransport(handler)
    return execute_plan(_plan(doc), base_url="http://api", transport=transport)


def test_extract_then_substitute_into_next_request() -> None:
    seen: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read()) if request.content else None
        seen.append((request.method, request.url.path))
        if request.url.path == "/posts/5":
            return httpx.Response(200, json={"id": 5, "title": "orig"})
        # PUT step receives {id} substituted
        assert body is not None and body.get("id") == 5
        return httpx.Response(200, json={"id": 5, "title": "changed"})

    exec_ = _run(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "vars": {"post_id": 5},
                    "steps": [
                        {
                            "name": "get",
                            "request": {"method": "GET", "path": "/posts/{post_id}"},
                            "extract": {"id": "$.id"},
                            "expect": {"status_code": 200},
                        },
                        {
                            "name": "put",
                            "request": {
                                "method": "PUT",
                                "path": "/posts/{id}",
                                "body": {"id": "{id}", "title": "changed"},
                            },
                            "expect": {"status_code": 200},
                        },
                    ],
                }
            ],
        },
        handler,
    )
    test = exec_.tests[0]
    assert test.ok
    assert test.steps[0].status_code == 200
    assert "/posts/5" in seen[1][1]


def test_abort_stops_remaining_steps() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/fail":
            return httpx.Response(500)
        return httpx.Response(200)

    exec_ = _run(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [
                        {"name": "f", "request": {"method": "GET", "path": "/fail"},
                         "expect": {"status_code": 200}, "on_fail": "abort"},
                        {"name": "never", "request": {"method": "GET", "path": "/ok"}},
                    ],
                }
            ],
        },
        handler,
    )
    test = exec_.tests[0]
    assert not test.ok
    assert [s.name for s in test.steps] == ["f"]  # aborted before executing step 2
    assert calls == ["/fail"]  # second step not executed after abort


def test_continue_runs_next_step_but_marks_test_failed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/fail":
            return httpx.Response(500)
        return httpx.Response(200)

    exec_ = _run(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [
                        {"name": "f", "request": {"method": "GET", "path": "/fail"},
                         "expect": {"status_code": 200}, "on_fail": "continue"},
                        {"name": "after", "request": {"method": "GET", "path": "/ok"}},
                    ],
                }
            ],
        },
        handler,
    )
    test = exec_.tests[0]
    assert not test.ok
    assert calls == ["/fail", "/ok"]


def test_cleanup_runs_on_failure_and_does_not_affect_status() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": 1, "title": "orig", "body": "b", "userId": 1})

    # Scenario: step GET ok, cleanup PUT with extract-orig.
    exec_ = _run(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [
                        {
                            "name": "get",
                            "request": {"method": "GET", "path": "/posts/1"},
                            "extract": {"orig_title": "$.title"},
                        }
                    ],
                    "cleanup": [
                        {
                            "name": "restore",
                            "request": {
                                "method": "PUT",
                                "path": "/posts/1",
                                "body": {"title": "{orig_title}"},
                            },
                            "expect": {"status_code": 200},
                        }
                    ],
                }
            ],
        },
        handler,
    )
    test = exec_.tests[0]
    assert test.ok
    assert len(test.cleanup) == 1
    assert not test.cleanup_warnings


def test_failing_cleanup_only_warns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/posts/1" and request.method == "GET":
            return httpx.Response(200, json={"id": 1, "title": "orig"})
        return httpx.Response(500)  # cleanup PUT fails

    exec_ = _run(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [{"name": "get", "request": {"method": "GET", "path": "/posts/1"}}],
                    "cleanup": [
                        {"name": "restore", "request": {"method": "PUT", "path": "/posts/1"},
                         "expect": {"status_code": 200}}
                    ],
                }
            ],
        },
        handler,
    )
    test = exec_.tests[0]
    assert test.ok  # step ok, cleanup failure doesn't fail the test
    assert len(test.cleanup_warnings) == 1


def test_extract_visible_in_cleanup() -> None:
    restore_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/posts/1":
            return httpx.Response(200, json={"id": 1, "title": "orig"})
        restore_body.update(json.loads(request.read()))
        return httpx.Response(200)

    exec_ = _run(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [
                        {"name": "get", "request": {"method": "GET", "path": "/posts/1"},
                         "extract": {"orig_title": "$.title"}}
                    ],
                    "cleanup": [
                        {"name": "restore", "request": {"method": "PUT", "path": "/posts/1",
                         "body": {"title": "{orig_title}"}}}
                    ],
                }
            ],
        },
        handler,
    )
    assert exec_.tests[0].ok
    assert restore_body.get("title") == "orig"


def test_flat_v1_plan_works() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 1})

    exec_ = _run(
        {"service": "s", "tests": [{"name": "read", "request": {"method": "GET", "path": "/posts/1"},
                                    "expect": {"status_code": 200}}]},
        handler,
    )
    assert exec_.tests[0].ok


def test_status_code_mismatch_fails_step() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    exec_ = _run(
        {"service": "s", "tests": [
            {"name": "t", "steps": [{"request": {"method": "GET", "path": "/x"},
                                     "expect": {"status_code": 200}}]}]},
        handler,
    )
    assert not exec_.tests[0].ok


def test_check_eq_evaluated_via_kit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 2})

    exec_ = _run(
        {"service": "s", "tests": [
            {"name": "t", "steps": [{"request": {"method": "GET", "path": "/x"},
                                     "expect": {"checks": [{"op": "eq", "path": "$.total", "value": 2}]}}]}]},
        handler,
    )
    step = exec_.tests[0].steps[0]
    assert step.ok
    assert step.checks[0].ok


def test_check_failure_marks_step_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 5})

    exec_ = _run(
        {"service": "s", "tests": [
            {"name": "t", "steps": [{"request": {"method": "GET", "path": "/x"},
                                     "expect": {"status_code": 200,
                                                "checks": [{"op": "eq", "path": "$.total", "value": 2}]}}]}]},
        handler,
    )
    step = exec_.tests[0].steps[0]
    assert not step.ok
    assert not step.checks[0].ok


def test_network_error_handled_gracefully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    exec_ = _run(
        {"service": "s", "tests": [
            {"name": "t", "steps": [{"request": {"method": "GET", "path": "/x"}}]}]},
        handler,
    )
    step = exec_.tests[0].steps[0]
    assert not step.ok
    assert step.status_code is None
    assert "сетевая ошибка" in (step.error or "")
