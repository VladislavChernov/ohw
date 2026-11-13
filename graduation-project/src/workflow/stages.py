"""File-based pipeline stages shared by the CLI and the Streamlit UI.

The stage functions own the artifact contracts: the generation snapshot, the
JSON report and the charts. The CLI adds logging and exit codes, the UI adds
widgets and session state, but neither front duplicates the stage logic.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.generator.ollama_client import OllamaClient
from src.generator.pipeline import GenerationRequest, generate_scenarios
from src.generator.service import RetryNotice
from src.models import PipelineData
from src.quality.assessor import assess_quality, covered_requirement_ids
from src.reporting.report_generator import (
    generate_all_reports,
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
    
    # Add pipeline version for future compatibility
    snapshot_data = data.to_snapshot_dict()
    snapshot_data["pipeline_version"] = "0.1.0"
    
    path.write_text(
        json.dumps(snapshot_data, ensure_ascii=False, indent=2) + "\n",
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
    
    # Validate pipeline version
    if raw.get("pipeline_version") != "0.1.0":
        raise ValueError(
            f"Pipeline version mismatch: expected 0.1.0, got {raw.get('pipeline_version')}"
        )
    
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


def report(data: PipelineData, output_dir: Path) -> dict[str, str]:
    """Write the JSON and Markdown reports and return their paths."""
    data.refresh_averages()
    return generate_all_reports(data, output_dir)
