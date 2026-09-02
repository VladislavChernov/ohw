"""Tests for the PlanCodec registry and the v3 codec (parity)."""

from __future__ import annotations

import json

import pytest

from json_testgen_advanced.plan import (
    DEFAULT_VERSION,
    PlanCodecV3,
    TestPlan,
    get_codec,
    load_plan,
    register_codec,
    validate_plan_schema,
)

_V3_PLAN: dict = {
    "service": "jsonplaceholder",
    "tests": [
        {
            "name": "update_post_roundtrip",
            "steps": [
                {
                    "name": "get",
                    "request": {"method": "GET", "path": "/posts/{post_id}"},
                    "extract": {"orig_title": "$.title"},
                    "expect": {"status_code": 200},
                },
            ],
        }
    ],
}

_FLAT_V1_PLAN: dict = {
    "service": "s",
    "tests": [
        {
            "name": "read_post",
            "request": {"method": "GET", "path": "/posts/1"},
            "expect": {"status_code": 200},
        }
    ],
}


def test_matches_v3_and_flat_v1_true() -> None:
    codec = PlanCodecV3()
    assert codec.matches(_V3_PLAN)
    assert codec.matches(_FLAT_V1_PLAN)


def test_matches_foreign_structure_false() -> None:
    codec = PlanCodecV3()
    assert not codec.matches({})
    assert not codec.matches({"some": "other"})
    assert not codec.matches([1, 2, 3])
    assert not codec.matches("not a dict")


def test_get_codec_resolves_v3_explicit_and_default() -> None:
    explicit = get_codec("v3", {})
    default = get_codec(None, _V3_PLAN)
    assert explicit.version == "v3"
    assert default.version == "v3"
    assert DEFAULT_VERSION == "v3"


def test_get_codec_decode_produces_expected_plan() -> None:
    codec = get_codec(None, _V3_PLAN)
    plan = codec.decode(_V3_PLAN)
    assert plan.service == "jsonplaceholder"
    assert len(plan.tests) == 1
    assert plan.tests[0].steps[0].request.method == "GET"


def test_get_codec_unknown_version_raises() -> None:
    with pytest.raises(ValueError):
        get_codec("v99", {})
    # version hint on the payload also resolves through the registry
    with pytest.raises(ValueError):
        get_codec(None, {"version": "v404", "service": "s", "tests": []})


def test_parity_decode_equals_legacy_parse() -> None:
    text = (
        '{"service": "s", "tests": [{"name": "t", '
        '"steps": [{"request": {"method": "GET", "path": "/posts/1"}}]}]}'
    )
    legacy = TestPlan.from_dict(json.loads(text))
    plan = load_plan(text)
    assert plan == legacy


def test_validate_delegates_to_codec() -> None:
    assert not validate_plan_schema({}).ok
    assert validate_plan_schema(_V3_PLAN).ok


def test_register_and_get_custom_codec() -> None:
    class _Other(PlanCodecV3):
        version = "v9"

    register_codec("v9", _Other())
    try:
        assert get_codec("v9", {}).version == "v9"
    finally:
        # restore default registry state for other tests
        register_codec("v3", PlanCodecV3())
