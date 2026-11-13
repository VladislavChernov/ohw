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
    validate_quality_weights,
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


def _cases_to_json(cases: list[TestCase]) -> list[dict[str, Any]]:
    return [case.model_dump(mode="json") for case in cases]


def _cases_from_json(raw: list[dict[str, Any]] | None) -> list[TestCase]:
    return [TestCase(**case) for case in raw or []]


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
    def from_snapshot_dict(cls, raw: dict[str, Any]) -> "ScenarioResult":
        return cls(
            filename=raw["filename"],
            source_path=raw.get("source_path", ""),
            artifact_type=raw.get("artifact_type", "testcases"),
            output_file=raw.get("output_file"),
            rendered=raw.get("rendered", ""),
            generations=list(raw.get("generations") or []),
            cases=_cases_from_json(raw.get("cases")),
            prompt=raw.get("prompt", ""),
            attempts=int(raw.get("attempts", 1)),
            quality=QualityMetrics.from_dict(raw.get("quality") or {}),
            requirement_ids=list(raw.get("requirement_ids") or []),
            covered_requirements=list(raw.get("covered_requirements") or []),
            prompt_sha256=raw.get("prompt_sha256", ""),
            runs=int(raw.get("runs", 1)),
            error=raw.get("error"),
        )

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
    def from_report_dict(cls, raw: dict[str, Any]) -> "ScenarioResult":
        return cls(
            filename=raw.get("filename", "unknown"),
            source_path="",
            artifact_type=raw.get("artifact_type", "testcases"),
            output_file=raw.get("output_file"),
            rendered="",
            generations=[],
            cases=_cases_from_json(raw.get("case_summaries")),
            prompt="",
            attempts=int(raw.get("attempts", 1)),
            quality=QualityMetrics.from_dict(raw.get("quality") or {}),
            requirement_ids=list(raw.get("requirement_ids") or []),
            covered_requirements=list(raw.get("cases_with_requirements") or []),
            prompt_sha256=raw.get("prompt_sha256", ""),
            runs=int(raw.get("runs", 1)),
            error=raw.get("error"),
        )


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _quality_weights_from_json(raw: dict[str, Any]) -> dict[str, float]:
    """Read optional weights; legacy files fall back to the defaults."""
    value = raw.get("quality_weights")
    if value is None:
        return dict(DEFAULT_QUALITY_WEIGHTS)
    if not isinstance(value, dict):
        raise ValueError("quality_weights должен быть объектом")
    return validate_quality_weights(value)


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
    def from_snapshot_dict(cls, raw: dict[str, Any]) -> "PipelineData":
        data = cls(
            input_dir=raw.get("input_dir", ""),
            artifact_type=raw.get("artifact_type", "testcases"),
            model=raw.get("model", ""),
            runs=int(raw.get("runs", 1)),
            count=int(raw.get("count", 10)),
            temperature=float(raw.get("temperature", 0.7)),
            normalize=bool(raw.get("normalize", False)),
            presets=list(raw.get("presets") or []),
            generated_at=raw.get("generated_at", ""),
            scenarios=[
                ScenarioResult.from_snapshot_dict(item)
                for item in raw.get("scenarios") or []
            ],
            errors=list(raw.get("errors") or []),
            quality_weights=_quality_weights_from_json(raw),
        )
        data.refresh_averages()
        return data

    def to_report_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project": "graduation-project",
            "version": "0.1.0",
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
    def from_report_dict(cls, raw: dict[str, Any]) -> "PipelineData":
        data = cls(
            input_dir=raw.get("input_dir", ""),
            artifact_type=raw.get("artifact_type", "testcases"),
            model=raw.get("model", ""),
            runs=int(raw.get("runs", 1)),
            count=int(raw.get("count", 10)),
            temperature=float(raw.get("temperature", 0.7)),
            normalize=bool(raw.get("normalize", False)),
            presets=list(raw.get("presets") or []),
            generated_at=raw.get("generated_at", ""),
            scenarios=[
                ScenarioResult.from_report_dict(item)
                for item in raw.get("scenarios") or []
            ],
            errors=list(raw.get("errors") or []),
            averages=dict(raw.get("averages") or {}),
            charts=dict(raw.get("charts") or {}),
            quality_weights=_quality_weights_from_json(raw),
        )
        if not data.averages:
            data.refresh_averages()
        return data
