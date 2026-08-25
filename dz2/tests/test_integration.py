import os
from pathlib import Path

import pytest

import ai_testgen.cli as cli_module
from ai_testgen.cli import main


def _ollama_reachable() -> bool:
    import httpx

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        return httpx.get(f"{base_url.rstrip('/')}/api/version", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OLLAMA_MODEL"),
        reason="OLLAMA_MODEL is not set",
    ),
    pytest.mark.skipif(not _ollama_reachable(), reason="ollama is not reachable"),
]


def test_real_generation_n10(tmp_path: Path) -> None:
    input_dir = Path(__file__).parent.parent / "examples" / "input"
    output_dir = tmp_path / "output"

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--max-retries",
            "3",
        ]
    )

    assert code == cli_module.EXIT_OK

    reports = sorted(output_dir.glob("*.md"))
    assert [r.name for r in reports] == ["auth.md", "cart.md"]

    for report in reports:
        content = report.read_text(encoding="utf-8")
        case_count = content.count("### TC-")
        assert case_count == 10, f"{report.name}: expected 10 cases, got {case_count}"
        assert "## Позитивные сценарии" in content, report.name
        assert "## Негативные сценарии" in content, report.name
