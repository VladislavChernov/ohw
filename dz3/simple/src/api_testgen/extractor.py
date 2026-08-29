"""Extract generated code from LLM response and save to file."""

from __future__ import annotations

import re
from pathlib import Path


def extract_code(response: str) -> str:
    """Extract Python code from LLM response.

    Handles markdown fences and bare code blocks.
    """
    # Try to extract from ```python ... ``` fences
    match = re.search(r"```python\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Try to extract from ``` ... ``` fences (no language tag)
    match = re.search(r"```\s*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    # If no fences, try to extract def test_ blocks
    code_lines: list[str] = []
    in_code = False
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            in_code = True
        if in_code:
            code_lines.append(line)
        if stripped.startswith("def test_") and not in_code:
            in_code = True
            code_lines.append(line)

    if code_lines:
        return "\n".join(code_lines).strip()

    # Fallback: return as-is (LLM might have generated clean code)
    return response.strip()


def save_code(code: str, output_dir: Path) -> Path:
    """Save generated code to a .py file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "generated_tests.py"
    path.write_text(code, encoding="utf-8")
    return path
