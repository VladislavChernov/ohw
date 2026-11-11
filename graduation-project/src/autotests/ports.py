"""Ports of the API-test seam.

An adapter implements :class:`ApiTestRunner`. The workflow only depends on
this protocol, so a future DZ3 advanced adapter can be added without touching
the pipeline, the report or the models.
"""

from typing import Protocol, runtime_checkable

from src.autotests.models import ApiTestReport, ApiTestRequest


class ApiTestError(RuntimeError):
    """The API-test phase cannot start or cannot be completed."""


@runtime_checkable
class ApiTestRunner(Protocol):
    """Executes API tests and returns a finished report."""

    name: str

    def run(self, request: ApiTestRequest) -> ApiTestReport: ...
