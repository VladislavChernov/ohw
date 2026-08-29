"""Data models for API test generator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Endpoint:
    """Single API endpoint from OpenAPI spec."""

    method: str
    path: str
    summary: str = ""
    request_body_schema: dict | None = None
    response_schema: dict | None = None
    response_codes: list[str] = field(default_factory=list)


@dataclass
class GeneratedTest:
    """A single generated test from LLM."""

    name: str
    method: str
    path: str
    request_data: dict | None = None
    expected_status: int | None = None
    description: str = ""


@dataclass
class TestResult:
    """Result of running a single test."""

    name: str
    passed: bool
    status_code: int | None = None
    response_body: dict | str | None = None
    error: str | None = None
    duration_ms: float = 0
