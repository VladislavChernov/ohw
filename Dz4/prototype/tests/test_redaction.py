"""L5-02: redaction секретов в логах (review_2 §5, critical_review №4, fast_review2 §7).

Проверяем:
- ``redact_secrets``: regex-красакция X-API-Key / Authorization / password / token / secret /
  neo4j_password (ключ=value → ключ=[REDACTED]), а также подстановку известных значений
  секретов из env.
- ``RedactingFilter``: msg/args/exc_text красятся при ``logging.Filter.filter(record)``.
- ``install_redaction``: фильтр навешивается на логгеры uvicorn + root (один раз).
"""

from __future__ import annotations

import logging

import pytest

from graphrag_proto.security import (
    RedactingFilter,
    install_redaction,
    redact_secrets,
)


class TestRedactSecrets:
    def test_dict_repr_x_api_key_value_is_redacted(self) -> None:
        src = "request = {'X-API-Key': 'changeme'}"
        assert redact_secrets(src) == "request = {'X-API-Key': '[REDACTED]'}"

    def test_env_style_x_api_key_value_is_redacted(self) -> None:
        assert redact_secrets("X-API-Key=changeme") == "X-API-Key=[REDACTED]"

    def test_authorization_header_value_is_redacted(self) -> None:
        assert redact_secrets("Authorization: Bearer super-secret-token") == \
            "Authorization: [REDACTED] super-secret-token"

    def test_password_env_value_is_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEO4J_PASSWORD", "very_secret_pw")
        assert redact_secrets("neo4j_password=very_secret_pw") == "neo4j_password=[REDACTED]"

    def test_known_value_is_redacted_in_any_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_API_KEY", "s3cr3t-k3y")
        out = redact_secrets("connection failed: header={'x-api-key': 's3cr3t-k3y'}")
        assert "s3cr3t-k3y" not in out
        assert "[REDACTED]" in out

    def test_neo4j_password_regex(self) -> None:
        text = "neo4j_password: 'graphrag'"
        result = redact_secrets(text)
        assert result == "neo4j_password: '[REDACTED]'"

    def test_empty_string_returns_empty(self) -> None:
        assert redact_secrets("") == ""

    def test_no_false_positive_on_max_tokens(self) -> None:
        src = "max_tokens: 8192"
        assert redact_secrets(src) == src


class TestRedactingFilter:
    def test_message_is_redacted(self) -> None:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="X-API-Key = mysecret", args=(), exc_info=None,
        )
        RedactingFilter().filter(record)
        assert "mysecret" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_args_are_redacted(self) -> None:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="sending %s", args=("X-API-Key: mysecret",), exc_info=None,
        )
        RedactingFilter().filter(record)
        assert "mysecret" not in record.args[0]  # type: ignore[index]

    def test_exc_text_is_redacted(self) -> None:
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="fail", args=(), exc_info=None,
        )
        record.exc_text = "X-API-Key: leak-value"
        RedactingFilter().filter(record)
        assert "leak-value" not in record.exc_text  # type: ignore[union-attr]

    def test_filter_returns_true(self) -> None:
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname="", lineno=0,
            msg="ok", args=(), exc_info=None,
        )
        assert RedactingFilter().filter(record) is True


class TestInstallRedaction:
    def test_adds_filter_once(self) -> None:
        install_redaction()
        root = logging.getLogger()
        assert any(isinstance(f, RedactingFilter) for f in root.filters)
        # вызов второй раз не дублирует
        install_redaction()
        assert sum(isinstance(f, RedactingFilter) for f in root.filters) == 1