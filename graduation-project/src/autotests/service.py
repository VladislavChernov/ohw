"""API-test phase service: pick a runner, execute it, persist the report."""

import json
from pathlib import Path

from src.autotests.disabled import DisabledApiTestRunner
from src.autotests.models import ApiTestReport, ApiTestRequest
from src.autotests.ports import ApiTestRunner

API_TESTS_NAME = "api-tests.json"


def resolve_runner() -> ApiTestRunner:
    """Return the active runner.

    A future DZ3 advanced adapter is registered here; until then the disabled
    runner keeps the contract honest: ``--api-tests`` fails with a clear
    message instead of silently doing nothing.
    """
    return DisabledApiTestRunner()


def run_api_tests(
    request: ApiTestRequest, runner: ApiTestRunner | None = None
) -> ApiTestReport:
    """Execute one API-test run through the selected runner."""
    selected = runner or resolve_runner()
    return selected.run(request)


def write_api_tests(report: ApiTestReport, output_dir: Path) -> Path:
    """Write ``api-tests.json`` and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / API_TESTS_NAME
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_api_tests(output_dir: Path) -> ApiTestReport | None:
    """Read ``api-tests.json`` when the phase ran earlier, otherwise ``None``."""
    path = output_dir / API_TESTS_NAME
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ApiTestReport.from_dict(raw)
