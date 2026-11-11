"""Reporting phase: JSON source of truth plus a Markdown presentation."""

from src.reporting.report_generator import (
    METRIC_NOTES,
    METRIC_TITLES,
    REPORT_JSON_NAME,
    REPORT_MARKDOWN_NAME,
    generate_all_reports,
    load_pipeline_data,
    render_markdown,
    write_json_report,
    write_markdown_report,
)

__all__ = [
    "METRIC_NOTES",
    "METRIC_TITLES",
    "REPORT_JSON_NAME",
    "REPORT_MARKDOWN_NAME",
    "generate_all_reports",
    "load_pipeline_data",
    "render_markdown",
    "write_json_report",
    "write_markdown_report",
]
