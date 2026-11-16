"""Pipeline data structures shared by the generator, assessment and reports.

Two serialisation formats are used:

* a *snapshot* (``generation.json``) keeps everything the assessment phase needs
  to recompute the metrics, including the rendered text of every run;
* a *report* (``quality-metrics.json``) is the source of truth for the
  visualization and reporting phases and contains the actual test cases.

Keeping both means the stages can be executed separately without re-running the
model, while the report stays small and human-readable.
"""

from dataclasses import dataclass, field
from typing import Any

from src.config import (
    DEFAULT_QUALITY_WEIGHTS,
    METRIC_KEYS,
    PIPELINE_VERSION,
    PROJECT_VERSION,
    validate_quality_weights,
)
from src.contracts import (
    REPORT_NAME,
    SNAPSHOT_NAME,
    QualityMetricsPayload,
    ScenarioReport,
    ScenarioSnapshot,
    load_report_document,
    load_scenario_report,
    load_scenario_snapshot,
    load_snapshot_document,
)
from src.generator.models import TestCase

QUALITY_THRESHOLD = 0.5


@dataclass(frozen=True)
class QualityMetrics:
    """Four NLP metrics plus a weighted overall score.

    A metric is ``None`` when it cannot be measured (for example coverage when
    the requirement file has no ``BR-N`` ids). ``None`` is kept distinct from
    zero so reports can print ``n/a`` instead of a fake measurement.
    """

    readability: float | None = None
    clarity_goals: float | None = None
    scenario_coverage: float | None = None
    consistency: float | None = None
    overall: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "readability": self.readability,
            "clarity_goals": self.clarity_goals,
            "scenario_coverage": self.scenario_coverage,
            "consistency": self.consistency,
            "overall": self.overall,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QualityMetrics":
        return cls(
            readability=raw.get("readability"),
            clarity_goals=raw.get("clarity_goals"),
            scenario_coverage=raw.get("scenario_coverage"),
            consistency=raw.get("consistency"),
            overall=raw.get("overall"),
        )


def _metrics_from_payload(
    payload: QualityMetricsPayload | None,
) -> QualityMetrics:
    metrics = payload if payload is not None else QualityMetricsPayload()
    return QualityMetrics.from_dict(metrics.model_dump())


def _cases_to_json(cases: list[TestCase]) -> list[dict[str, Any]]:
    return [case.model_dump(mode="json") for case in cases]


@dataclass
class ScenarioResult:
    """One requirement file processed by the generation phase."""

    filename: str
    source_path: str
    artifact_type: str
    output_file: str | None
    rendered: str
    cases: list[TestCase]
    prompt: str
    attempts: int
    quality: QualityMetrics
    requirement_ids: list[str]
    covered_requirements: list[str]
    prompt_sha256: str
    runs: int
    generations: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def successful(self) -> bool:
        return (
            self.quality.overall is not None
            and self.quality.overall >= QUALITY_THRESHOLD
        )

    def to_snapshot_dict(self) -> dict[str, Any]:
        """Full state required to recompute the metrics later."""
        return {
            "filename": self.filename,
            "source_path": self.source_path,
            "artifact_type": self.artifact_type,
            "output_file": self.output_file,
            "rendered": self.rendered,
            "generations": self.generations,
            "cases": _cases_to_json(self.cases),
            "prompt": self.prompt,
            "attempts": self.attempts,
            "quality": self.quality.to_dict(),
            "requirement_ids": self.requirement_ids,
            "covered_requirements": self.covered_requirements,
            "prompt_sha256": self.prompt_sha256,
            "runs": self.runs,
            "error": self.error,
        }

    @classmethod
    def from_snapshot_model(cls, raw: ScenarioSnapshot) -> "ScenarioResult":
        return cls(
            filename=raw.filename,
            source_path=raw.source_path,
            artifact_type=raw.artifact_type,
            output_file=raw.output_file,
            rendered=raw.rendered,
            generations=list(raw.generations or []),
            cases=list(raw.cases or []),
            prompt=raw.prompt,
            attempts=raw.attempts,
            quality=_metrics_from_payload(raw.quality),
            requirement_ids=list(raw.requirement_ids or []),
            covered_requirements=list(raw.covered_requirements or []),
            prompt_sha256=raw.prompt_sha256,
            runs=raw.runs,
            error=raw.error,
        )

    @classmethod
    def from_snapshot_dict(
        cls, raw: dict[str, Any], *, source: str = SNAPSHOT_NAME
    ) -> "ScenarioResult":
        return cls.from_snapshot_model(load_scenario_snapshot(raw, source))

    def to_report_dict(self, index: int) -> dict[str, Any]:
        """Compact representation used by ``quality-metrics.json``."""
        return {
            "id": index,
            "filename": self.filename,
            "artifact_type": self.artifact_type,
            "output_file": self.output_file,
            "quality": self.quality.to_dict(),
            "requirement_ids": self.requirement_ids,
            "cases_with_requirements": self.covered_requirements,
            "prompt_sha256": self.prompt_sha256,
            "attempts": self.attempts,
            "runs": self.runs,
            "case_summaries": _cases_to_json(self.cases),
            "error": self.error,
        }

    @classmethod
    def from_report_model(cls, raw: ScenarioReport) -> "ScenarioResult":
        return cls(
            filename=raw.filename,
            source_path="",
            artifact_type=raw.artifact_type,
            output_file=raw.output_file,
            rendered="",
            generations=[],
            cases=list(raw.case_summaries or []),
            prompt="",
            attempts=raw.attempts,
            quality=_metrics_from_payload(raw.quality),
            requirement_ids=list(raw.requirement_ids or []),
            covered_requirements=list(raw.cases_with_requirements or []),
            prompt_sha256=raw.prompt_sha256,
            runs=raw.runs,
            error=raw.error,
        )

    @classmethod
    def from_report_dict(
        cls, raw: dict[str, Any], *, source: str = REPORT_NAME
    ) -> "ScenarioResult":
        return cls.from_report_model(load_scenario_report(raw, source))


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _weights_from_document(
    value: dict[str, Any] | None, *, provided: bool, source: str
) -> dict[str, float]:
    """Read optional weights; legacy files fall back to the defaults."""
    if not provided:
        return dict(DEFAULT_QUALITY_WEIGHTS)
    if not isinstance(value, dict):
        raise ValueError(f"{source}: quality_weights должен быть объектом")
    try:
        return validate_quality_weights(value)
    except ValueError as exc:
        raise ValueError(f"{source}: {exc}") from None


@dataclass
class PipelineData:
    """Everything the reporting phase needs, independent of the generator."""

    input_dir: str = ""
    artifact_type: str = "testcases"
    model: str = ""
    runs: int = 1
    count: int = 10
    temperature: float = 0.7
    normalize: bool = False
    presets: list[str] = field(default_factory=list)
    generated_at: str = ""
    scenarios: list[ScenarioResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    averages: dict[str, float | None] = field(default_factory=dict)
    charts: dict[str, str] = field(default_factory=dict)
    quality_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_QUALITY_WEIGHTS)
    )

    @property
    def successful(self) -> int:
        return sum(1 for scenario in self.scenarios if scenario.successful)

    @property
    def unsuccessful(self) -> int:
        return len(self.scenarios) - self.successful

    @property
    def overall(self) -> float | None:
        return self.averages.get("overall")

    def refresh_averages(self) -> dict[str, float | None]:
        """Average every metric across scenarios, keeping ``None`` for gaps."""
        result: dict[str, float | None] = {}
        for key in (*METRIC_KEYS, "overall"):
            values = [
                value
                for value in (getattr(item.quality, key) for item in self.scenarios)
                if value is not None
            ]
            result[key] = _average(values)
        self.averages = result
        return result

    def _metadata(self) -> dict[str, Any]:
        return {
            "input_dir": self.input_dir,
            "artifact_type": self.artifact_type,
            "model": self.model,
            "runs": self.runs,
            "count": self.count,
            "temperature": self.temperature,
            "normalize": self.normalize,
            "presets": list(self.presets),
            "generated_at": self.generated_at,
            "quality_weights": dict(self.quality_weights),
        }

    def to_snapshot_dict(self) -> dict[str, Any]:
        return {
            "pipeline_version": PIPELINE_VERSION,
            **self._metadata(),
            "scenarios": [
                scenario.to_snapshot_dict() for scenario in self.scenarios
            ],
            "errors": list(self.errors),
        }

    @classmethod
    def from_snapshot_dict(
        cls, raw: dict[str, Any], *, source: str = SNAPSHOT_NAME
    ) -> "PipelineData":
        document = load_snapshot_document(raw, source)
        data = cls(
            input_dir=document.input_dir,
            artifact_type=document.artifact_type,
            model=document.model,
            runs=document.runs,
            count=document.count,
            temperature=document.temperature,
            normalize=document.normalize,
            presets=list(document.presets or []),
            generated_at=document.generated_at,
            scenarios=[
                ScenarioResult.from_snapshot_model(item)
                for item in document.scenarios or []
            ],
            errors=list(document.errors or []),
            quality_weights=_weights_from_document(
                document.quality_weights,
                provided="quality_weights" in document.model_fields_set,
                source=source,
            ),
        )
        data.refresh_averages()
        return data

    def to_report_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project": "graduation-project",
            "version": PROJECT_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            **self._metadata(),
            "total_scenarios": len(self.scenarios),
            "successful": self.successful,
            "unsuccessful": self.unsuccessful,
            "quality_threshold": QUALITY_THRESHOLD,
            "averages": dict(self.averages),
            "charts": dict(self.charts),
            "errors": list(self.errors),
            "scenarios": [
                scenario.to_report_dict(index)
                for index, scenario in enumerate(self.scenarios, start=1)
            ],
        }
        return payload

    @classmethod
    def from_report_dict(
        cls, raw: dict[str, Any], *, source: str = REPORT_NAME
    ) -> "PipelineData":
        document = load_report_document(raw, source)
        data = cls(
            input_dir=document.input_dir,
            artifact_type=document.artifact_type,
            model=document.model,
            runs=document.runs,
            count=document.count,
            temperature=document.temperature,
            normalize=document.normalize,
            presets=list(document.presets or []),
            generated_at=document.generated_at,
            scenarios=[
                ScenarioResult.from_report_model(item)
                for item in document.scenarios or []
            ],
            errors=list(document.errors or []),
            averages=dict(document.averages or {}),
            charts=dict(document.charts or {}),
            quality_weights=_weights_from_document(
                document.quality_weights,
                provided="quality_weights" in document.model_fields_set,
                source=source,
            ),
        )
        if not data.averages:
            data.refresh_averages()
        return data
