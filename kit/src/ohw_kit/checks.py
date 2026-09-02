"""Reference evaluator for the expect-check DSL (JSONPath subset).

Checks come from an LLM-produced test plan; this module evaluates them
deterministically against a live HTTP response. The supported JSONPath
subset (no external dependencies): ``$`` root, ``.key`` object access,
``[i]`` array index, ``[*]`` all items, ``..key`` recursive descent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["CheckResult", "evaluate", "json_path"]

_OPS = {"eq", "len_eq", "contains", "fields_eq", "type"}


class CheckError(ValueError):
    """A check (or its JSONPath) is malformed — feedback-worthy."""


@dataclass
class CheckResult:
    op: str
    path: str
    expected: object
    actual: object
    ok: bool

    def as_line(self) -> str:
        """Human-readable one-liner for reports."""
        mark = "OK" if self.ok else "FAIL"
        return (
            f"[{mark}] {self.op} `{self.path}`: "
            f"ожидалось {self.expected!r}, получено {self.actual!r}"
        )


def json_path(body: Any, path: str) -> list[Any]:
    """Resolve a JSONPath subset against ``body``; returns all matches."""
    if not path.startswith("$"):
        raise CheckError("", path, "JSONPath должен начинаться с `$`")
    current: list[Any] = [body]
    for token in _tokens(path[1:]):
        nxt: list[Any] = []
        for node in current:
            if token == "..":
                continue  # combined with the next token by _tokens
            if token == "[*]":
                if isinstance(node, list):
                    nxt.extend(node)
                continue
            if token.startswith(".."):
                nxt.extend(_descend_all(node, token[2:]))
                continue
            if token.startswith("."):
                key = token[1:]
                if isinstance(node, dict):
                    if key in node:
                        nxt.append(node[key])
                elif isinstance(node, list) and key.isdigit():
                    idx = int(key)
                    if idx < len(node):
                        nxt.append(node[idx])
                continue
            if token.startswith("["):
                inner = token[1:-1]
                if isinstance(node, list):
                    if inner == "*":
                        nxt.extend(node)
                    elif inner.isdigit() and int(inner) < len(node):
                        nxt.append(node[int(inner)])
                continue
            raise CheckError("", path, f"не поддерживается фрагмент: {token!r}")
        current = nxt
    return current


def _tokens(rest: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(rest):
        if rest.startswith("..", i):
            j = i + 2
            while j < len(rest) and (rest[j].isalnum() or rest[j] == "_"):
                j += 1
            tokens.append(rest[i:j])
            i = j
            continue
        if rest[i] == ".":
            j = i + 1
            while j < len(rest) and (rest[j].isalnum() or rest[j] == "_"):
                j += 1
            tokens.append(rest[i:j])
            i = j
            continue
        if rest[i] == "[":
            j = rest.index("]", i)
            tokens.append(rest[i : j + 1])
            i = j + 1
            continue
        raise CheckError("", rest, f"не поддерживается фрагмент пути: {rest[i:]!r}")
    return tokens


def _descend_all(node: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        if key in node:
            found.append(node[key])
        for value in node.values():
            found.extend(_descend_all(value, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_descend_all(item, key))
    return found


def _first(op: str, path: str, body: Any) -> Any:
    matches = json_path(body, path)
    if not matches:
        raise CheckError(op, path, "путь не разрешился ни в одно значение")
    return matches[0]


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


def _evaluate_one(check: dict, *, body: Any) -> CheckResult:
    op = str(check.get("op", ""))
    path = str(check.get("path", "$"))
    value = check.get("value")
    if op not in _OPS:
        return CheckResult(op, path, value, f"неизвестная операция {op!r}", False)
    try:
        if op == "eq":
            actual = _first(op, path, body)
            return CheckResult(op, path, value, actual, actual == value)
        if op == "len_eq":
            actual = _first(op, path, body)
            ok = isinstance(value, int) and len(actual) == value
            return CheckResult(op, path, value, f"len={len(actual)}", ok)
        if op == "contains":
            matches = json_path(body, path)
            if not matches:
                raise CheckError(op, path, "путь не разрешился ни в одно значение")
            ok = any(value in m for m in matches)
            return CheckResult(op, path, value, matches if not ok else value, ok)
        if op == "fields_eq":
            actual = _first(op, path, body)
            ok = isinstance(actual, dict) and set(actual) == set(value or [])
            shown = sorted(actual) if isinstance(actual, dict) else actual
            return CheckResult(op, path, value, shown, ok)
        # op == "type"
        actual = _first(op, path, body)
        if value not in _TYPE_MAP:
            return CheckResult(op, path, value, type(actual).__name__, False)
        ok = isinstance(actual, _TYPE_MAP[value]) and (
            value != "integer" or not isinstance(actual, bool)
        )
        return CheckResult(op, path, value, type(actual).__name__, ok)
    except (CheckError, TypeError, KeyError, IndexError):
        return CheckResult(op, path, value, "<путь не разрешился>", False)


def evaluate(checks: list[dict], *, status_code: int, body: Any) -> list[CheckResult]:
    """Evaluate expect-checks against an HTTP response.

    ``status_code`` is available for future ops; ``body`` may be any parsed
    JSON value (dict, list, scalar) or ``None``.
    """
    return [_evaluate_one(check, body=body) for check in checks]
