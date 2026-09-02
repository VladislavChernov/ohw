"""Report assembly for the advanced core (no pytest involved).

Turns a ``PlanExecution`` into a human-readable Markdown report: the test
name straight from the plan, one block per step with every expect check
(expected vs actual), an explicit FAILURES section without raw tracebacks,
and the documentation mode in the header.
"""

from __future__ import annotations

from ohw_kit.render import render_markdown

from json_testgen_advanced.core import PlanExecution


def build_report(
    execution: PlanExecution,
    *,
    model: str,
    schema_version: str,
) -> str:
    """Render the execution into a Markdown report string."""
    lines: list[str] = [
        "## Итог",
        "",
        f"- **Сервис:** `{execution.service}`",
        f"- **Режим документации:** `{execution.doc_mode}`",
        f"- **Модель:** `{model}`",
        f"- **Схема плана:** `{schema_version}`",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Тестов | {execution.total} |",
        f"| Прошло | {execution.passed} |",
        f"| Упало | {execution.failed} |",
        "",
    ]

    for test in execution.tests:
        status = "OK" if test.ok else "FAILED"
        lines.append(f"## `{test.name}` — `{status}`")
        if test.description:
            lines.append("")
            lines.append(test.description)
        lines.append("")
        lines.append("| Шаг | результат | код | мс |")
        lines.append("|---|---|---|---|")
        for step in test.steps:
            mark = "OK" if step.ok else "FAIL"
            code = step.status_code if step.status_code is not None else "—"
            lines.append(f"| **{step.name or step.method} {step.path}** | {mark} | {code} | {step.duration_ms} |")
        for step in test.steps:
            if not step.ok and step.error:
                lines.append("")
                lines.append(f"  - Шаг «{step.name or step.method} {step.path}»: {step.error}")
            for check in step.checks:
                if check.ok:
                    continue
                lines.append(f"      - {check.as_line()}")
        if test.cleanup_warnings:
            lines.append("")
            lines.append("**Cleanup warnings:**")
            for warning in test.cleanup_warnings:
                lines.append(f"  - ⚠️ {warning}")
        lines.append("")

    failures = [t for t in execution.tests if not t.ok]
    if failures:
        lines.append("## Не прошло")
        lines.append("")
        for test in failures:
            lines.append(f"- **`{test.name}`**")
            for step in test.steps:
                if not step.ok and step.error:
                    lines.append(f"  - шаг «{step.name or step.method} {step.path}»: {step.error}")
        lines.append("")

    return render_markdown("\n".join(lines), source_name="advanced-report", title="Отчёт прогона тестов")
