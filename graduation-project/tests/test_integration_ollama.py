"""Live end-to-end test against a running Ollama service.

Disabled by default. Enable explicitly:

    RUN_OLLAMA_INTEGRATION=1 uv run pytest -m integration

The requirement source is derived from this project's SDD
(``tests/e2e/input/sdd-requirements.md``), so the model is tested on a real
document of the project rather than on an abstract sample.

Inside Docker on the shared network use ``OLLAMA_BASE_URL=http://ohw-ollama:11434``.
"""

import json
import os
from pathlib import Path

import pytest

from src.workflow.main import main

pytestmark = pytest.mark.integration

RUN = os.environ.get("RUN_OLLAMA_INTEGRATION") == "1"
INPUT_DIR = Path("tests/e2e/input")


@pytest.mark.skipif(not RUN, reason="set RUN_OLLAMA_INTEGRATION=1 to call Ollama")
def test_full_pipeline_from_sdd_requirements(tmp_path: Path) -> None:
    code = main(
        [
            "--input-dir",
            str(INPUT_DIR),
            "--output-dir",
            str(tmp_path),
            "--count",
            "3",
            "--runs",
            "2",
            "--temperature",
            "0.2",
            "--base-url",
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            "--model",
            os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct"),
            "--timeout",
            os.environ.get("OLLAMA_TIMEOUT", "600"),
        ]
    )
    assert code == 0, "pipeline failed, see the CLI log above"

    scenarios = list((tmp_path / "scenarios").glob("*.md"))
    assert scenarios, "no generated artifacts"
    assert (tmp_path / "generation.json").is_file()
    assert (tmp_path / "report.md").is_file()
    assert (tmp_path / "charts").is_dir()

    report = json.loads(
        (tmp_path / "quality-metrics.json").read_text(encoding="utf-8")
    )
    assert report["total_scenarios"] == 1
    assert report["scenarios"][0]["case_summaries"], "case_summaries are empty"
    assert report["scenarios"][0]["runs"] == 2
    assert report["averages"]["overall"] is not None
    assert report["scenarios"][0]["quality"]["consistency"] is not None
