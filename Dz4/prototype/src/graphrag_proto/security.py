"""Минимальный redaction-фильтр для логов (L5-02, прототип).

Подменяет значения секретов (`X-API-Key`, `Authorization`, `api_key`, `password`,
`neo4j_password`, `token`, `secret`) и известные значения секретов из env
(`AUTH_API_KEY`/`GRAPH_AUTH_API_KEY`/`NEO4J_PASSWORD`) на `[REDACTED]`.

Установка: ``install_redaction()`` — вызывается в ``main()`` каждого сервиса.
"""

from __future__ import annotations

import logging
import os
import re

_SECRET_ENV_KEYS = (
    "AUTH_API_KEY",
    "GRAPH_AUTH_API_KEY",
    "NEO4J_PASSWORD",
    "GLOSSARY_API_KEY",
    "LLM_API_KEY",
    "RERANKER_API_KEY",
)

_KEYS_PATTERN = re.compile(
    r"""
    (?P<delim>
        (?P<key>
            x[- _]?api[- _]?key
            | authorization
            | api[_-]?key
            | password
            | passwd
            | secret
            | neo4j[_-]?password
            | token
        )
        ['"]?                     # закрывающая кавычка ключа в dict-repr
        \s*[=:]\s*
        ["']?                     # открывающая кавычка значения
    )
    (?P<value>[^"'\s;,}\]]+)
    (?P<close>["']?)
""",
    re.VERBOSE | re.IGNORECASE,
)


def _known_secret_values() -> list[str]:
    return [
        val
        for key in _SECRET_ENV_KEYS
        if (val := os.environ.get(key, ""))
    ]


def redact_secrets(text: str) -> str:
    if not text:
        return text

    # 1) regex: знаем ключ, заменяем значение
    result = _KEYS_PATTERN.sub(r"\g<delim>[REDACTED]\g<close>", text)

    # 2) known values: заменяем полные токены/пароли, попавшие в строку
    for secret in _known_secret_values():
        result = result.replace(secret, "[REDACTED]")

    return result


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_secrets(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact_secrets(str(v)) if isinstance(v, str) else v
                                   for k, v in record.args.items()}
                else:
                    record.args = tuple(
                        redact_secrets(str(a)) if isinstance(a, str) else a
                        for a in record.args
                    )
            # форматирование traceback — отдельно, чтобы убрать секреты из
            # exc-выводов httpx/requests (repr заголовков с X-API-Key)
            if record.exc_text is not None:
                record.exc_text = redact_secrets(str(record.exc_text))
            if record.exc_info is not None and record.exc_text is None:
                record.exc_text = redact_secrets(
                    logging.Formatter().formatException(record.exc_info)
                )
                record.exc_info = None
        except Exception:  # noqa: BLE001, S110 — фильтр не должен ронять логирование
            pass
        return True


def install_redaction() -> None:
    """Навесить ``RedactingFilter`` на ``uvicorn.*`` и корневой логгер."""
    _handler = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactingFilter) for f in logger.filters):
            logger.addFilter(_handler)
