"""Run pytest on generated test file and collect results."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_PAIR_RE = re.compile(r"(\d+) (passed|failed|error)")


def _parse_counts(stdout: str) -> tuple[int, int, int]:
    """Extract (passed, failed, errors) from the pytest summary line.

    Prefers the final summary ("1 failed, 5 passed in 5.29s"); counting
    substrings is only a fallback because pytest's `short test summary info`
    block repeats "FAILED <test>" lines and would double-count.
    """
    summary_lines = [
        line for line in stdout.splitlines() if _PAIR_RE.search(line) and " in " in line
    ]
    if summary_lines:
        counts = {"passed": 0, "failed": 0, "error": 0}
        for num, kind in _PAIR_RE.findall(summary_lines[-1]):
            counts[kind] = int(num)
        return counts["passed"], counts["failed"], counts["error"]
    return stdout.count("PASSED"), stdout.count("FAILED"), stdout.count("ERROR")


_FAIL_LINE_RE = re.compile(r"^FAILED\s+(\S+)\s*-\s*(.*)$")


def _parse_failures(stdout: str) -> list[tuple[str, str]]:
    """Extract (test node, short reason) pairs from the short summary block."""
    failures = []
    in_summary = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if "short test summary" in stripped and stripped.startswith("="):
            in_summary = True
            continue
        if in_summary:
            m = _FAIL_LINE_RE.match(stripped)
            if m:
                failures.append((m.group(1), m.group(2).strip()))
    return failures


def _humanize_reason(reason: str, limit: int = 220) -> str:
    """Make a pytest failure reason readable: strip noise, truncate values."""
    reason = re.sub(r"^AssertionError:\s*", "assert failed: ", reason)
    if len(reason) > limit:
        reason = reason[:limit].rsplit(" ", 1)[0] + " …(truncated)"
    return reason


def _failed_tests_section(results: dict) -> list[str]:
    """Human-readable 'Failed tests' block; one bullet per failure."""
    failures = _parse_failures(results.get("stdout", ""))
    if not failures:
        return []
    lines = ["## Failed tests", ""]
    for node, reason in failures:
        short_node = node.split("::")[-1].replace("output/generated_tests.py::", "")
        lines.append(f"- **`{short_node}`** — {_humanize_reason(reason)}")
    lines += [
        "",
        "Full tracebacks with payloads are in the [Pytest output](#pytest-output) appendix.",
        "",
    ]
    return lines


def run_pytest(test_file: Path) -> dict:
    """Run pytest on the given file and return structured results."""
    result = subprocess.run(  # noqa: PLW1510
        [
            sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short",
            "--no-header", "-p", "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    passed, failed, errors = _parse_counts(result.stdout)

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


def format_report(results: dict) -> str:
    """Format a human-readable report for the console."""
    lines = [
        "=" * 60,
        "  API Test Generation Report",
        "=" * 60,
        "",
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


def format_report_markdown(results: dict) -> str:
    """Format a report as Markdown and persist it to the output dir.

    The run report is a deliverable: it records how the generated API tests
    performed, so it is saved to the output directory (and from there into git)
    alongside the generated test file.
    """
    # Honest verdict: the run only succeeded when pytest itself finished
    # cleanly AND actually ran tests. A missing pytest module (exit 1 with
    # zero tests) or an empty collection (exit 5) must NOT report OK.
    status = "OK" if results["exit_code"] == 0 and results["passed"] > 0 else "FAILED"
    md = [
        "# API Test Generation Report",
        "",
        f"- **Result:** `{status}`",
        f"- **Test file:** `{results['file']}`",
        f"- **Exit code:** `{results['exit_code']}`",
    ]
    if results.get("model"):
        md.append(f"- **Model:** `{results['model']}`")
    md += [
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total tests | {results['total']} |",
        f"| Passed | {results['passed']} |",
        f"| Failed | {results['failed']} |",
        f"| Errors | {results['errors']} |",
        "",
    ]
    md += _failed_tests_section(results)
    if results["stdout"]:
        md += [
            "## Pytest output",
            "",
            "```",
            results["stdout"].rstrip("\n"),
            "```",
            "",
        ]
    if results["stderr"]:
        md += [
            "## Stderr",
            "",
            "```",
            results["stderr"].rstrip("\n"),
            "```",
            "",
        ]
    return "\n".join(md).rstrip() + "\n"


def save_report(results: dict, output_dir: Path) -> Path:
    """Write the Markdown run report to the output dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.md"
    path.write_text(format_report_markdown(results), encoding="utf-8")
    return path
