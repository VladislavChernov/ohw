"""Prompt loader — reads template from a file in the input directory."""

from __future__ import annotations

from pathlib import Path

from api_testgen.models import Endpoint


def load_prompt_template(input_dir: Path) -> str:
    """Read the prompt template from input/prompt.txt."""
    path = input_dir / "prompt.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def build_prompt(template: str, endpoints: list[Endpoint], base_url: str) -> str:
    """Fill the prompt template with the base URL and endpoint list."""
    return template.format(base_url=base_url, endpoints=_format_endpoints(endpoints))


def _format_endpoints(endpoints: list[Endpoint]) -> str:
    """Format endpoints for the prompt."""
    lines = []
    for ep in endpoints:
        line = f"- {ep.method} {ep.path}"
        if ep.summary:
            line += f" — {ep.summary}"
        if ep.request_body_schema:
            props = ep.request_body_schema.get("properties", {})
            if props:
                fields = ", ".join(f"{k}: {v.get('type', '?')}" for k, v in props.items())
                line += f" [body: {fields}]"
        lines.append(line)
    return "\n".join(lines)
