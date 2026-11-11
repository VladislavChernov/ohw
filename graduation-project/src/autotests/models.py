"""Data structures for the API-test seam.

The seam is transport-agnostic: a runner receives a request and returns a
finished report. Adapter-specific types (a JSON test plan, HTTP steps) stay
inside the adapter and never leak into the pipeline.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ApiTestRequest:
    """Everything a runner needs to execute API tests."""

    base_url: str
    contract_path: Path | None = None
    cases: int = 5
    timeout: float = 600.0
    model: str = ""


@dataclass(frozen=True)
class ApiCaseResult:
    """Outcome of one API test case."""

    name: str
    method: str = ""
    path: str = ""
    ok: bool = False
    status_code: int | None = None
    duration_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "ok": self.ok,
            "status_code": self.status_code,
            "duration_ms": round(self.duration_ms, 3),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ApiCaseResult":
        return cls(
            name=str(raw.get("name", "")),
            method=str(raw.get("method", "")),
            path=str(raw.get("path", "")),
            ok=bool(raw.get("ok", False)),
            status_code=raw.get("status_code"),
            duration_ms=float(raw.get("duration_ms", 0.0)),
            error=raw.get("error"),
        )


@dataclass
class ApiTestReport:
    """Source of truth for the ``api-tests.json`` file and the report section."""

    runner: str
    base_url: str
    model: str
    generated_at: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0
    cases: list[ApiCaseResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_cases(
        cls,
        *,
        runner: str,
        base_url: str,
        model: str,
        generated_at: str,
        cases: list[ApiCaseResult],
        errors: list[str] | None = None,
        duration_ms: float = 0.0,
    ) -> "ApiTestReport":
        passed = sum(1 for case in cases if case.ok)
        return cls(
            runner=runner,
            base_url=base_url,
            model=model,
            generated_at=generated_at,
            total=len(cases),
            passed=passed,
            failed=len(cases) - passed,
            duration_ms=duration_ms,
            cases=list(cases),
            errors=list(errors or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner": self.runner,
            "base_url": self.base_url,
            "model": self.model,
            "generated_at": self.generated_at,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "duration_ms": round(self.duration_ms, 3),
            "cases": [case.to_dict() for case in self.cases],
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ApiTestReport":
        return cls(
            runner=str(raw.get("runner", "")),
            base_url=str(raw.get("base_url", "")),
            model=str(raw.get("model", "")),
            generated_at=str(raw.get("generated_at", "")),
            total=int(raw.get("total", 0)),
            passed=int(raw.get("passed", 0)),
            failed=int(raw.get("failed", 0)),
            duration_ms=float(raw.get("duration_ms", 0.0)),
            cases=[
                ApiCaseResult.from_dict(item) for item in raw.get("cases") or []
            ],
            errors=list(raw.get("errors") or []),
        )
