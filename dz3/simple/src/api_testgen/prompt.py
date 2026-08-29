"""Prompt builder for LLM."""

from __future__ import annotations

from api_testgen.models import Endpoint


def build_prompt(endpoints: list[Endpoint], base_url: str) -> str:
    """Build prompt asking LLM to generate pytest tests.

    The prompt includes the OpenAPI spec and asks for 4 tests (one per CRUD type).
    """
    endpoints_desc = _format_endpoints(endpoints)

    return f"""You are a senior QA engineer. Generate exactly 4 pytest test functions
for the following REST API. Use the `requests` library.

API base URL: {base_url}

API Endpoints:
{endpoints_desc}

Requirements:
- Generate exactly 4 test functions, one for each CRUD operation:
  1. CREATE (POST) — create a new resource
  2. READ (GET) — get an existing resource
  3. UPDATE (PUT) — update an existing resource
  4. DELETE (DELETE) — delete a resource
- Pick the most appropriate endpoint for each operation from the spec above.
- Each test MUST check:
  - The HTTP status code is correct (based on the spec responses)
  - The response body contains the expected fields and types
- Use realistic test data based on the request body schemas.
- Do NOT use fixtures or helpers — each test must be self-contained.
- Output ONLY valid Python code, no markdown fences, no commentary.
- Each test function must start with `def test_` (pytest convention).
- Import `requests` at the top of the file.
"""


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
