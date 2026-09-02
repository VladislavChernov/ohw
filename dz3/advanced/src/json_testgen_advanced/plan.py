"""Test-plan data model, parsing/normalization and validation.

The plan is the LLM-produced contract: a list of test scenarios built from
steps (ordered HTTP requests with extract/expect) plus an optional cleanup
list. The core below never touches this module's internals as dicts; it
works on the normalized ``TestPlan`` dataclasses.

``TestPlan`` is the single internal model shared by every plan version; a
:class:`PlanCodec` is only a translator from a specific on-the-wire format
(v3 and future versions) to that model. The schema version is selected
through the codec registry (:func:`get_codec`), never hard-wired into the
core.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ohw_kit.jsonreply import ValidationResult

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

_SCHEMA = Path(__file__).parent / "plan_schema" / "v3.json"
DEFAULT_VERSION = "v3"


class PlanParseError(ValueError):
    """The plan JSON is structurally invalid (not just semantically)."""


@dataclass
class RequestSpec:
    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    body: object = None

    @classmethod
    def from_dict(cls, raw: object) -> RequestSpec:
        if not isinstance(raw, dict):
            raise PlanParseError("request должен быть объектом")
        method = raw.get("method")
        path = raw.get("path")
        if not isinstance(method, str) or not method:
            raise PlanParseError("request.method обязателен (строка)")
        if not isinstance(path, str) or not path:
            raise PlanParseError("request.path обязателен (строка)")
        headers = raw.get("headers") or {}
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise PlanParseError("request.headers — объект строка→строка")
        return cls(method=method.upper(), path=path, headers=dict(headers), body=raw.get("body"))


@dataclass
class ExpectSpec:
    status_code: int | None = None
    checks: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: object) -> ExpectSpec:
        checks: list[dict[str, object]] = []
        status_code: int | None = None
        if isinstance(raw, dict):
            sc = raw.get("status_code")
            if sc is not None:
                if not isinstance(sc, int) or isinstance(sc, bool):
                    raise PlanParseError("expect.status_code — целое число")
                status_code = sc
            cs = raw.get("checks") or []
            if not isinstance(cs, list):
                raise PlanParseError("expect.checks — массив")
            for c in cs:
                if not isinstance(c, dict):
                    raise PlanParseError("элемент expect.checks — объект")
                checks.append(dict(c))
        return cls(status_code=status_code, checks=checks)


@dataclass
class StepSpec:
    name: str
    request: RequestSpec
    extract: dict[str, str] = field(default_factory=dict)
    expect: ExpectSpec = field(default_factory=ExpectSpec)
    on_fail: str = "abort"

    @classmethod
    def from_dict(cls, raw: object) -> StepSpec:
        if not isinstance(raw, dict):
            raise PlanParseError("шаг (step) должен быть объектом")
        request = RequestSpec.from_dict(raw.get("request"))
        extract = raw.get("extract") or {}
        if not isinstance(extract, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in extract.items()
        ):
            raise PlanParseError("step.extract — объект строка→строка (JSONPath)")
        on_fail = raw.get("on_fail", "abort")
        if on_fail not in ("abort", "continue"):
            raise PlanParseError("step.on_fail — только abort|continue")
        return cls(
            name=str(raw.get("name", "")),
            request=request,
            extract=dict(extract),
            expect=ExpectSpec.from_dict(raw.get("expect")),
            on_fail=on_fail,
        )


@dataclass
class TestSpec:
    name: str
    description: str
    steps: list[StepSpec] = field(default_factory=list)
    cleanup: list[StepSpec] = field(default_factory=list)
    vars: dict[str, object] = field(default_factory=dict)
    provisional: bool = False

    @property
    def is_mutating(self) -> bool:
        return any(s.request.method in _MUTATING for s in self.steps)

    @classmethod
    def from_dict(cls, raw: object) -> TestSpec:
        if not isinstance(raw, dict):
            raise PlanParseError("test должен быть объектом")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise PlanParseError("test.name обязателен")
        steps_raw = raw.get("steps")
        # Normalize flat v1 form (single request/expect on the test) to one step.
        if steps_raw is None:
            steps_raw = []
        if not isinstance(steps_raw, list):
            raise PlanParseError("test.steps — массив")
        steps = [StepSpec.from_dict(s) for s in steps_raw]
        if not steps and ("request" in raw or "expect" in raw):
            steps = [StepSpec.from_dict({"name": name, "request": raw.get("request"),
                                         "expect": raw.get("expect")})]

        cleanup_raw = raw.get("cleanup") or []
        if not isinstance(cleanup_raw, list):
            raise PlanParseError("test.cleanup — массив")
        cleanup = [StepSpec.from_dict(s) for s in cleanup_raw]

        vars_raw = raw.get("vars") or {}
        if not isinstance(vars_raw, dict):
            raise PlanParseError("test.vars — объект")

        return cls(
            name=name,
            description=str(raw.get("description", "")),
            steps=steps,
            cleanup=cleanup,
            vars=dict(vars_raw),
            provisional=bool(raw.get("provisional", False)),
        )


@dataclass
class TestPlan:
    service: str
    tests: list[TestSpec] = field(default_factory=list)
    doc_mode: str = "unknown"

    __test__ = False  # pytest must not treat this importable class as a test

    @classmethod
    def from_dict(cls, raw: object, *, doc_mode: str = "unknown") -> TestPlan:
        if not isinstance(raw, dict):
            raise PlanParseError("план — JSON-объект")
        service = raw.get("service")
        if not isinstance(service, str) or not service:
            raise PlanParseError("plan.service обязателен")
        tests_raw = raw.get("tests")
        if not isinstance(tests_raw, list) or not tests_raw:
            raise PlanParseError("plan.tests — непустой массив")
        return cls(
            service=service,
            tests=[TestSpec.from_dict(t) for t in tests_raw],
            doc_mode=doc_mode,
        )


def schema_version() -> str:
    """Return the schema version the loader is compatible with."""
    return DEFAULT_VERSION


class PlanCodec(Protocol):
    """Translator from one on-the-wire plan format to the :class:`TestPlan` model.

    ``TestPlan`` is the single internal model shared by all versions; a codec is
    only a "format -> model" bridge. Implementations must expose the version
    identity, its schema file, a way to detect whether a raw payload belongs to
    this version, and both a strict decoder and a structural validator.
    """

    version: str
    schema_file: Path

    def matches(self, raw: object) -> bool: ...
    def decode(self, raw: object, *, doc_mode: str = "unknown") -> TestPlan: ...
    def validate(self, raw: object) -> ValidationResult: ...


class PlanCodecV3:
    """Codec for the v3 plan format (service/tests, with flat v1 normalization).

    Designed for parity: it performs exactly the parsing/normalization the
    legacy ``TestPlan.from_dict`` did, with no behavioral change.
    """

    version = "v3"
    schema_file = _SCHEMA

    def matches(self, raw: object) -> bool:
        """Detect the v3 format by its signature (with flat v1 fallback)."""
        if not isinstance(raw, dict):
            return False
        if "service" in raw or (isinstance(raw.get("tests"), list) and raw["tests"]):
            return True
        # Flat v1 form: a test carries request/expect directly on it.
        tests = raw.get("tests")
        if isinstance(tests, list):
            for t in tests:
                if isinstance(t, dict) and ("request" in t or "expect" in t):
                    return True
        return False

    def decode(self, raw: object, *, doc_mode: str = "unknown") -> TestPlan:
        return TestPlan.from_dict(raw, doc_mode=doc_mode)

    def validate(self, raw: object) -> ValidationResult:
        return _validate_v3_schema(raw)


_REGISTRY: dict[str, PlanCodec] = {DEFAULT_VERSION: PlanCodecV3()}


def _plan_version_hint(raw: object) -> str | None:
    """A ``version`` field on the plan overrides signature-based detection."""
    if isinstance(raw, dict):
        version = raw.get("version")
        if isinstance(version, str):
            return version
    return None


def register_codec(version: str, codec: PlanCodec) -> None:
    """Register (or replace) a codec for ``version`` in the plan registry."""
    _REGISTRY[version] = codec


def get_codec(version: str | None, raw: object) -> PlanCodec:
    """Resolve a codec by explicit version, signature hint, or default.

    Unknown versions raise ``ValueError`` — no silent fallback, so the prompt,
    the engine and the schema cannot drift apart quietly.
    """
    v = version or _plan_version_hint(raw) or DEFAULT_VERSION
    try:
        return _REGISTRY[v]
    except KeyError:
        raise ValueError(f"unknown plan schema version: {v}") from None


# --- extraction of {var} placeholders ---------------------------------

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def referenced_vars(value: object) -> set[str]:
    """Collect ``{var}`` placeholders from any JSON-ish value (walk strings)."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(_PLACEHOLDER_RE.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            found |= referenced_vars(v)
    elif isinstance(value, list):
        for v in value:
            found |= referenced_vars(v)
    return found


def plan_referenced_vars(test: TestSpec) -> set[str]:
    """All ``{var}`` names used anywhere in steps and cleanup."""
    used: set[str] = set()
    for step in test.steps + test.cleanup:
        used |= referenced_vars(step.request.path)
        used |= referenced_vars(step.request.headers)
        used |= referenced_vars(step.request.body)
    return used


def _define_vars(test: TestSpec) -> set[str]:
    defined = set(test.vars)
    for step in test.steps:
        defined.update(step.extract.keys())
    return defined


def validate_plan(plan: TestPlan, required_resources: list[str]) -> ValidationResult:
    """Validate a parsed plan: structure, coverage, cleanup discipline.

    Returns ``ValidationResult.ok`` plus human-readable ``issues`` worded as
    feedback for the model feedback-loop.
    """
    issues: list[str] = []

    # (b) mutating scenario must have cleanup (or be a provisional create).
    for t in plan.tests:
        if t.is_mutating and not t.cleanup and not t.provisional:
            issues.append(
                f"В сценарии '{t.name}' есть мутирующие шаги (POST/PUT/PATCH/DELETE), "
                f"но нет cleanup и нет provisional. Добавь cleanup-откат либо "
                f"provisional: true для create."
            )

    # (c) every {var} used in cleanup must be defined in vars or extracted in steps.
    for t in plan.tests:
        defined = _define_vars(t)
        for step in t.cleanup:
            missing = referenced_vars(step.request.path) | referenced_vars(
                step.request.headers
            ) | referenced_vars(step.request.body)
            for var in sorted(missing - defined):
                issues.append(
                    f"В cleanup сценария '{t.name}' используется {{ {var} }}, "
                    f"но переменная не задана в vars и не извлекается в steps. "
                    f"Добавь её в vars или в extract какого-либо шага."
                )

    # (a) coverage: each required resource+verb pair appears in the plan.
    if required_resources:
        covered: set[tuple[str, str]] = set()
        for t in plan.tests:
            for s in t.steps:
                path = s.request.path.lower()
                for resource in required_resources:
                    if f"/{resource}" in path:
                        covered.add((resource, s.request.method))
        for resource in required_resources:
            verbs = [m for (r, m) in covered if r == resource]
            # At least one verb per resource (CRUD-ish coverage without requiring all).
            if resource and not verbs:
                issues.append(
                    f"В плане нет ни одного шага по ресурсу /{resource}. "
                    f"Покрой ресурс {resource} хотя бы одним шагом."
                )

    return ValidationResult.success() if not issues else ValidationResult.failure(issues)


def _validate_v3_schema(raw: object) -> ValidationResult:
    """Structural validation of the plan JSON against the v3 schema (manual)."""
    issues: list[str] = []
    if not isinstance(raw, dict):
        return ValidationResult.failure(["План должен быть JSON-объектом."])
    if "service" not in raw:
        issues.append("Отсутствует поле plan.service.")
    if "tests" not in raw or not isinstance(raw["tests"], list) or not raw["tests"]:
        issues.append("Отсутствует или пуст plan.tests (непустой массив).")
    else:
        for i, t in enumerate(raw["tests"]):
            if not isinstance(t, dict):
                issues.append(f"test['{i}'] — не объект.")
                continue
            if "name" not in t:
                issues.append(f"test['{i}'] — нет name.")
            if "steps" not in t and "request" not in t:
                issues.append(
                    f"test['{t.get('name', i)}'] — нет steps (нет и плоской формы request)."
                )
    return ValidationResult.success() if not issues else ValidationResult.failure(issues)


def validate_plan_schema(raw: object) -> ValidationResult:
    """Structural validation of the plan JSON for the resolved schema version."""
    return get_codec(None, raw).validate(raw)


def load_plan(text: str, *, doc_mode: str = "unknown") -> TestPlan:
    """Parse plan JSON text into a validated ``TestPlan``.

    Raises ``JsonReplyError`` for unparseable JSON and ``PlanParseError`` for
    structurally invalid plans.
    """
    raw = json.loads(text)  # let JsonReplyError be raised upstream for parsing
    return get_codec(None, raw).decode(raw, doc_mode=doc_mode)
