"""File-based pipeline stages shared by the CLI and the Streamlit UI.

The stage functions own the artifact contracts: the generation snapshot, the
JSON report, the charts and ``api-tests.json``. The CLI adds logging and exit
codes, the UI adds widgets and session state, but neither front duplicates the
stage logic.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.autotests.models import ApiTestReport, ApiTestRequest
from src.autotests.ports import ApiTestRunner
from src.autotests.service import load_api_tests, run_api_tests, write_api_tests
from src.generator.ollama_client import OllamaClient
from src.generator.pipeline import GenerationRequest, generate_scenarios
from src.generator.service import RetryNotice
from src.models import PipelineData
from src.quality.assessor import assess_quality, covered_requirement_ids
from src.reporting.report_generator import (
    REPORT_JSON_NAME,
    generate_all_reports,
    load_pipeline_data,
    write_json_report,
)
from src.visualization.charts import create_all_charts

SNAPSHOT_NAME = "generation.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def snapshot_path(output_dir: Path) -> Path:
    return output_dir / SNAPSHOT_NAME


def write_snapshot(data: PipelineData, output_dir: Path) -> Path:
    """Write ``generation.json`` and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(output_dir)
    path.write_text(
        json.dumps(data.to_snapshot_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_snapshot(output_dir: Path) -> PipelineData:
    """Read ``generation.json`` or explain which phase is missing."""
    path = snapshot_path(output_dir)
    if not path.is_file():
        raise FileNotFoundError(
            f"generation snapshot not found: {path}; run the generate phase first"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return PipelineData.from_snapshot_dict(raw)


def generate(
    request: GenerationRequest,
    client: OllamaClient,
    *,
    on_retry: Callable[[RetryNotice], None] | None = None,
) -> PipelineData:
    """Run generation and persist the snapshot the assessment phase reads."""
    outcome = generate_scenarios(request, client, on_retry=on_retry)
    data = PipelineData(
        input_dir=str(request.input_dir),
        artifact_type=request.artifact_type.value,
        model=client.model,
        runs=request.runs,
        count=request.count,
        temperature=request.temperature,
        normalize=request.normalize,
        presets=list(request.presets),
        generated_at=_now(),
        scenarios=outcome.scenarios,
        errors=outcome.errors,
    )
    data.refresh_averages()
    write_snapshot(data, request.output_dir)
    return data


def assess(data: PipelineData, output_dir: Path) -> PipelineData:
    """Recompute the metrics from the stored rendered text and runs."""
    for scenario in data.scenarios:
        generations = scenario.generations or [scenario.rendered]
        scenario.quality = assess_quality(
            scenario.rendered,
            scenario.requirement_ids,
            generations if len(generations) > 1 else None,
        )
        scenario.covered_requirements = covered_requirement_ids(
            scenario.rendered, scenario.requirement_ids
        )
        scenario.runs = len(generations)
    data.refresh_averages()
    write_json_report(data, output_dir)
    return data


def visualize(data: PipelineData, output_dir: Path) -> PipelineData:
    """Build the charts and persist their paths in the JSON report."""
    data.refresh_averages()
    data.charts = create_all_charts(data, output_dir)
    if data.charts:
        write_json_report(data, output_dir)
    return data


def api_tests(
    request: ApiTestRequest,
    runner: ApiTestRunner,
    output_dir: Path,
    data: PipelineData | None = None,
) -> tuple[ApiTestReport, PipelineData | None]:
    """Run the API-test phase and keep the JSON report in sync.

    When the phase runs alone, an existing ``quality-metrics.json`` is updated;
    when it runs before that file exists, only ``api-tests.json`` is written.
    """
    report = run_api_tests(request, runner=runner)
    write_api_tests(report, output_dir)

    if data is None and (output_dir / REPORT_JSON_NAME).is_file():
        data = load_pipeline_data(output_dir)
    if data is not None:
        data.api_tests = report
        write_json_report(data, output_dir)
    return report, data


def report(data: PipelineData, output_dir: Path) -> dict[str, str]:
    """Write the JSON and Markdown reports and return their paths."""
    data.refresh_averages()
    if data.api_tests is None:
        data.api_tests = load_api_tests(output_dir)
    return generate_all_reports(data, output_dir)
