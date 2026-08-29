"""Run pytest on generated test file and collect results."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_pytest(test_file: Path) -> dict:
    """Run pytest on the given file and return structured results."""
    result = subprocess.run(  # noqa: PLW1510
        [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "--no-header"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    passed = result.stdout.count("PASSED")
    failed = result.stdout.count("FAILED")
    errors = result.stdout.count("ERROR")

    return {
        "file": str(test_file),
        "exit_code": result.returncode,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total": passed + failed + errors,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def format_report(results: dict, endpoint_count: int) -> str:
    """Format a human-readable report."""
    lines = [
        "=" * 60,
        "  API Test Generation Report",
        "=" * 60,
        "",
        f"  Endpoints parsed:   {endpoint_count}",
        f"  Tests generated:    {results['total']}",
        f"  Passed:             {results['passed']}",
        f"  Failed:             {results['failed']}",
        f"  Errors:             {results['errors']}",
        "",
        f"  Exit code:          {results['exit_code']}",
        "",
        "-" * 60,
        "  Pytest output:",
        "-" * 60,
        results["stdout"],
    ]
    if results["stderr"]:
        lines.extend(["", "  Stderr:", results["stderr"]])
    lines.append("=" * 60)
    return "\n".join(lines)
