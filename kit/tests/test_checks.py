"""Tests for ohw_kit.checks."""

from __future__ import annotations

import pytest

from ohw_kit.checks import CheckError, evaluate, json_path

BODY = {
    "posts": [
        {"id": 1, "userId": 1, "title": "a", "body": "x"},
        {"id": 2, "userId": 1, "title": "b", "body": "y"},
    ],
    "total": 2,
}


def test_json_path_basic_access() -> None:
    assert json_path(BODY, "$.total") == [2]
    assert json_path(BODY, "$.posts[0].id") == [1]
    assert json_path(BODY, "$.posts[*].title") == ["a", "b"]
    assert json_path(BODY, "$..id") == [1, 2]


def test_json_path_rejects_non_root() -> None:
    with pytest.raises(CheckError):
        json_path(BODY, "posts[0]")


def test_eq_and_len_eq() -> None:
    (r,) = evaluate([{"op": "eq", "path": "$.total", "value": 2}], status_code=200, body=BODY)
    assert r.ok
    (r,) = evaluate([{"op": "len_eq", "path": "$.posts", "value": 2}], status_code=200, body=BODY)
    assert r.ok
    (r,) = evaluate([{"op": "len_eq", "path": "$.posts", "value": 5}], status_code=200, body=BODY)
    assert not r.ok


def test_contains_and_fields_eq() -> None:
    (r,) = evaluate([{"op": "contains", "path": "$.posts[*].title", "value": "b"}], status_code=200, body=BODY)
    assert r.ok
    (r,) = evaluate(
        [{"op": "fields_eq", "path": "$.posts[0]", "value": ["id", "userId", "title", "body"]}],
        status_code=200, body=BODY,
    )
    assert r.ok
    (r,) = evaluate([{"op": "fields_eq", "path": "$.posts[0]", "value": ["id"]}], status_code=200, body=BODY)
    assert not r.ok


def test_type_check() -> None:
    (r,) = evaluate([{"op": "type", "path": "$.posts", "value": "array"}], status_code=200, body=BODY)
    assert r.ok
    (r,) = evaluate([{"op": "type", "path": "$.total", "value": "integer"}], status_code=200, body=BODY)
    assert r.ok


def test_unresolved_path_fails_gracefully() -> None:
    (r,) = evaluate([{"op": "eq", "path": "$.missing", "value": 1}], status_code=200, body=BODY)
    assert not r.ok
    assert r.actual == "<путь не разрешился>"


def test_unknown_op_fails_gracefully() -> None:
    (r,) = evaluate([{"op": "regex", "path": "$", "value": "x"}], status_code=200, body=BODY)
    assert not r.ok


def test_result_as_line() -> None:
    (r,) = evaluate([{"op": "eq", "path": "$.total", "value": 3}], status_code=200, body=BODY)
    line = r.as_line()
    assert "FAIL" in line and "$.total" in line and "3" in line
