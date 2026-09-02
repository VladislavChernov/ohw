"""Tests for plan parsing, normalization and validation."""

from __future__ import annotations

from json_testgen_advanced.plan import (
    TestPlan,
    plan_referenced_vars,
    referenced_vars,
    validate_plan,
    validate_plan_schema,
)


def _plan(doc: dict) -> TestPlan:
    from json_testgen_advanced.plan import TestPlan as TP

    return TP.from_dict(doc)


def test_parse_v3_scenario_with_cleanup() -> None:
    plan = _plan(
        {
            "service": "jsonplaceholder",
            "tests": [
                {
                    "name": "update_post_roundtrip",
                    "vars": {"post_id": 1},
                    "steps": [
                        {
                            "name": "get",
                            "request": {"method": "GET", "path": "/posts/{post_id}"},
                            "extract": {"orig_title": "$.title"},
                            "expect": {"status_code": 200},
                        },
                        {
                            "name": "put",
                            "request": {
                                "method": "PUT",
                                "path": "/posts/{post_id}",
                                "body": {"id": "{post_id}", "title": "changed"},
                            },
                            "expect": {"status_code": 200},
                        },
                    ],
                    "cleanup": [
                        {
                            "name": "restore",
                            "request": {
                                "method": "PUT",
                                "path": "/posts/{post_id}",
                                "body": {"title": "{orig_title}"},
                            },
                        }
                    ],
                }
            ],
        }
    )
    test = plan.tests[0]
    assert test.name == "update_post_roundtrip"
    assert len(test.steps) == 2
    assert len(test.cleanup) == 1
    assert test.steps[0].request.method == "GET"
    assert test.steps[0].expect.status_code == 200
    assert test.is_mutating


def test_flat_v1_form_normalized_to_one_step() -> None:
    plan = _plan(
        {
            "service": "s",
            "tests": [
                {
                    "name": "read_post",
                    "request": {"method": "GET", "path": "/posts/1"},
                    "expect": {"status_code": 200},
                }
            ],
        }
    )
    test = plan.tests[0]
    assert len(test.steps) == 1
    assert test.steps[0].request.method == "GET"
    assert test.steps[0].expect.status_code == 200


def test_referenced_vars_walks_nested() -> None:
    assert referenced_vars("/posts/{a}/x") == {"a"}
    assert referenced_vars({"id": "{a}", "n": 1}) == {"a"}
    assert referenced_vars(["{a}", "{b}"]) == {"a", "b"}
    assert referenced_vars("no vars") == set()


def test_plan_referenced_vars_collects_steps_and_cleanup() -> None:
    plan = _plan(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [
                        {"request": {"method": "GET", "path": "/{a}"}},
                    ],
                    "cleanup": [{"request": {"method": "PUT", "path": "/{b}"}}],
                }
            ],
        }
    )
    assert plan_referenced_vars(plan.tests[0]) == {"a", "b"}


def test_validate_plan_ok() -> None:
    plan = _plan(
        {
            "service": "s",
            "tests": [
                {
                    "name": "read",
                    "steps": [{"request": {"method": "GET", "path": "/posts/1"}}],
                }
            ],
        }
    )
    result = validate_plan(plan, required_resources=["posts"])
    assert result.ok


def test_validate_plan_coverage_required_resource() -> None:
    plan = _plan(
        {
            "service": "s",
            "tests": [
                {"name": "read", "steps": [{"request": {"method": "GET", "path": "/posts/1"}}]}
            ],
        }
    )
    result = validate_plan(plan, required_resources=["posts", "users"])
    assert not result.ok
    assert any("users" in issue for issue in result.issues)


def test_validate_plan_mutating_requires_cleanup_or_provisional() -> None:
    base = {
        "service": "s",
        "tests": [
            {
                "name": "create",
                "steps": [{"request": {"method": "POST", "path": "/posts", "body": {"title": "x"}}}],
            }
        ],
    }
    assert not validate_plan(_plan(base), []).ok
    with_cleanup = {
        "service": "s",
        "tests": [
            {
                "name": "create",
                "vars": {"id": 1},
                "steps": [{"request": {"method": "POST", "path": "/posts", "body": {"title": "x"}}}],
                "cleanup": [{"request": {"method": "DELETE", "path": "/posts/{id}"}}],
            }
        ],
    }
    assert validate_plan(_plan(with_cleanup), []).ok
    provisional = {
        "service": "s",
        "tests": [
            {
                "name": "create",
                "provisional": True,
                "steps": [{"request": {"method": "POST", "path": "/posts", "body": {"title": "x"}}}],
            }
        ],
    }
    assert validate_plan(_plan(provisional), []).ok


def test_validate_plan_cleanup_var_must_be_defined() -> None:
    plan = _plan(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [{"request": {"method": "GET", "path": "/posts/1"}}],
                    "cleanup": [{"request": {"method": "PUT", "path": "/posts/{id}"}}],
                }
            ],
        }
    )
    result = validate_plan(plan, [])
    assert not result.ok
    assert any("id" in issue for issue in result.issues)


def test_validate_plan_cleanup_var_defined_in_vars() -> None:
    plan = _plan(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "vars": {"id": 5},
                    "steps": [{"request": {"method": "GET", "path": "/posts/1"}}],
                    "cleanup": [{"request": {"method": "PUT", "path": "/posts/{id}"}}],
                }
            ],
        }
    )
    assert validate_plan(plan, []).ok


def test_validate_plan_cleanup_var_extracted_in_step() -> None:
    plan = _plan(
        {
            "service": "s",
            "tests": [
                {
                    "name": "t",
                    "steps": [
                        {
                            "request": {"method": "POST", "path": "/posts", "body": {"title": "x"}},
                            "extract": {"id": "$.id"},
                        }
                    ],
                    "cleanup": [{"request": {"method": "DELETE", "path": "/posts/{id}"}}],
                }
            ],
        }
    )
    assert validate_plan(plan, []).ok


def test_validate_plan_schema() -> None:
    assert not validate_plan_schema({}).ok
    assert not validate_plan_schema({"service": "s", "tests": []}).ok
    # steps array present (even empty) passes the structural schema check
    assert validate_plan_schema({"service": "s", "tests": [{"name": "t", "steps": []}]}).ok


def test_validate_plan_schema_missing_steps_and_request() -> None:
    result = validate_plan_schema({"service": "s", "tests": [{"name": "t"}]})
    assert not result.ok
