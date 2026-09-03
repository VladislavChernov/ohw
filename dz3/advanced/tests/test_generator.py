"""Tests for the plan-level feedback loop (mocked OllamaClient)."""

from __future__ import annotations

import json

import httpx
import pytest
from ohw_kit.ollama_client import OllamaClient

from json_testgen_advanced.generator import GenerationFailed, generate_plan

VALID_PLAN = {
    "service": "jsonplaceholder",
    "tests": [
        {"name": "read_posts", "steps": [{"request": {"method": "GET", "path": "/posts/1"}}]},
        {"name": "read_users", "steps": [{"request": {"method": "GET", "path": "/users/1"}}]},
    ],
}

PLAN_MISSING_USERS = {
    "service": "jsonplaceholder",
    "tests": [
        {"name": "read_posts", "steps": [{"request": {"method": "GET", "path": "/posts/1"}}]},
    ],
}


def _client(replies: list[str]) -> tuple[OllamaClient, list[str]]:
    prompts: list[str] = []
    index = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        prompts.append(payload["messages"][-1]["content"])
        reply = replies[index["i"]]
        index["i"] += 1
        return httpx.Response(200, json={"message": {"content": reply}})

    client = OllamaClient(base_url="http://mock", model="m", json_mode=True,
                          timeout=5.0, transport=httpx.MockTransport(handler))
    return client, prompts


def test_success_on_first_attempt() -> None:
    client, _ = _client([json.dumps(VALID_PLAN)])
    plan = generate_plan(client, "prompt", max_retries=3, required_resources=["posts", "users"])
    assert plan.service == "jsonplaceholder"
    assert len(plan.tests) == 2


def test_feedback_loop_mentions_missing_resource() -> None:
    client, prompts = _client([json.dumps(PLAN_MISSING_USERS), json.dumps(VALID_PLAN)])
    plan = generate_plan(client, "prompt", max_retries=3, required_resources=["posts", "users"])
    assert len(plan.tests) == 2
    assert len(prompts) == 2
    assert "Предыдущий JSON-план отклонён" in prompts[1]
    assert "users" in prompts[1]


def test_plan_parse_error_triggers_feedback() -> None:
    # request is a string, not an object -> PlanParseError (was a crash before fix)
    bad_request = {
        "service": "jsonplaceholder",
        "tests": [
            {"name": "bad", "steps": [{"request": "GET /posts/1"}]},
        ],
    }
    client, prompts = _client([json.dumps(bad_request), json.dumps(VALID_PLAN)])
    plan = generate_plan(client, "prompt", max_retries=3, required_resources=["posts", "users"])
    assert len(plan.tests) == 2
    assert len(prompts) == 2
    assert "Предыдущий JSON-план отклонён" in prompts[1]
    assert "request" in prompts[1]


def test_schema_invalid_then_valid() -> None:
    not_a_plan = json.dumps({"service": "x"})  # no tests -> schema issue
    client, prompts = _client([not_a_plan, json.dumps(VALID_PLAN)])
    plan = generate_plan(client, "prompt", max_retries=3, required_resources=["posts", "users"])
    assert len(plan.tests) == 2
    assert len(prompts) == 2


def test_exhaust_retries_raises() -> None:
    client, _ = _client([json.dumps(PLAN_MISSING_USERS), json.dumps(PLAN_MISSING_USERS),
                         json.dumps(PLAN_MISSING_USERS)])
    with pytest.raises(GenerationFailed):
        generate_plan(client, "prompt", max_retries=3, required_resources=["posts", "users"])


def test_mutating_without_cleanup_triggers_feedback() -> None:
    mutating_no_cleanup = {
        "service": "s",
        "tests": [
            {"name": "create", "steps": [{"request": {"method": "POST", "path": "/posts",
                                                       "body": {"title": "x"}}}]}
        ],
    }
    client, prompts = _client(
        [json.dumps(mutating_no_cleanup), json.dumps(VALID_PLAN)]
    )
    plan = generate_plan(client, "prompt", max_retries=3, required_resources=["posts", "users"])
    assert len(plan.tests) == 2
    assert "cleanup" in prompts[1] or "provisional" in prompts[1]
