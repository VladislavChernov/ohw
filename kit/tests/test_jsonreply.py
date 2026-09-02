"""Tests for ohw_kit.jsonreply."""

from __future__ import annotations

import pytest

from ohw_kit.jsonreply import JsonReplyError, ValidationResult, extract_json


def test_extract_bare_json_object() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_fenced_json() -> None:
    text = "Вот план:\n```json\n[{\"x\": 2}]\n```\n"
    assert extract_json(text) == [{"x": 2}]


def test_extract_rejects_scalar() -> None:
    with pytest.raises(JsonReplyError):
        extract_json("42")


def test_extract_rejects_prose_without_json() -> None:
    with pytest.raises(JsonReplyError):
        extract_json("Я не понял задачу, вот обычный текст.")


def test_error_message_is_model_directed() -> None:
    with pytest.raises(JsonReplyError) as exc:
        extract_json("no json here")
    assert "Перегенерируй" in str(exc.value)


def test_validation_result_factories() -> None:
    ok = ValidationResult.success()
    assert ok.ok and ok.issues == []
    bad = ValidationResult.failure(["нет DELETE", "нет users"])
    assert not bad.ok and len(bad.issues) == 2
