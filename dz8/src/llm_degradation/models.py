"""Data models for llm-degradation-lab.

Report models are pydantic models with ``extra="forbid"`` so a malformed
``report.json`` is rejected instead of silently accepted. ``NormalizedInput``
and ``PromptBundle`` follow the frozen dataclass contract from the TZ.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_testgen.models import ArtifactType
from ai_testgen.normalizer import NormalizerSettings
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class NormalizedInput:
    """Input after deterministic normalization by ``ai_testgen.normalizer``."""

    source: Path
    source_sha256: str
    encoding: str
    text: str
    text_sha256: str
    title: str
    url: str | None
    br_ids: tuple[str, ...]
    settings: NormalizerSettings
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PromptBundle:
    """System and user prompt built from an external Dz2 template."""

    artifact_type: ArtifactType
    system: str
    user: str
    prompt_sha256: str
    count: int
    presets: tuple[str, ...]


class RunParams(_StrictModel):
    temperature: float = 0.7
    seed: int | None = 42
    num_ctx: int = 4096
    num_predict: int = 512
    max_retries: int = 3


class ChatCall(_StrictModel):
    """One HTTP chat call, including retries."""

    index: int = 1
    system: str = ""
    user: str = ""
    temperature: float = 0.7
    json_mode: bool = False
    latency_ms: float = 0.0
    raw: str = ""
    error: str | None = None


class ArtifactMetrics(_StrictModel):
    raw_chars: int = 0
    raw_lines: int = 0
    raw_sha256: str = ""
    has_code_fence: bool = False
    json_valid: bool = False
    schema_valid: bool = False
    validator_pass: bool = False
    validator_issues: list[str] = Field(default_factory=list)
    cases_total: int = 0
    positive: int = 0
    negative: int = 0
    negative_ratio: float = 0.0
    count_matches: bool = False
    tc_ids: list[str] = Field(default_factory=list)
    tc_duplicates: list[str] = Field(default_factory=list)
    br_ids_output: list[str] = Field(default_factory=list)
    br_ids_source: list[str] = Field(default_factory=list)
    br_covered: list[str] = Field(default_factory=list)
    br_missing: list[str] = Field(default_factory=list)
    br_invented: list[str] = Field(default_factory=list)
    br_coverage: float = 0.0
    urls_output: list[str] = Field(default_factory=list)
    urls_invented: list[str] = Field(default_factory=list)
    claims_execution_results: bool = False
    claims_markers: list[str] = Field(default_factory=list)
    section_headings: list[str] = Field(default_factory=list)
    title_tokens: list[str] = Field(default_factory=list)
    required_sections_present: list[str] = Field(default_factory=list)
    required_sections_missing: list[str] = Field(default_factory=list)
    empty_sections: list[str] = Field(default_factory=list)
    truncated: bool = False
    token_count: int = 0
    latency_ms: float = 0.0
    attempts: int = 1


class RunRecord(_StrictModel):
    index: int
    cell: str | None = None
    started_at: str = ""
    latency_ms: float = 0.0
    attempts: int = 1
    raw_sha256: str = ""
    raw_chars: int = 0
    validator_pass: bool = False
    metrics: ArtifactMetrics = Field(default_factory=ArtifactMetrics)
    error: str | None = None


class ProbeResult(_StrictModel):
    id: str
    kind: str
    status: str
    critical: bool = False
    evidence: str = ""
    forbidden_hits: list[str] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)
    runs: int = 0
    failed_runs: int = 0
    error: str | None = None


class CanaryResult(_StrictModel):
    verdict: str = "fail"
    parse_error: str | None = None
    ids_exact: bool = False
    count_exact: bool = False
    url_exact: bool = False
    title_overlap: float = 0.0
    body_overlap_mean: float = 0.0
    body_overlap_min: float = 0.0
    extra_ids: list[str] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)
    llm_canonical: str = ""
    det_canonical: str = ""
    raw: str = ""


class CellSummary(_StrictModel):
    model: str
    digest: str | None = None
    params: RunParams = Field(default_factory=RunParams)
    runs: int = 0
    pass_at_1: float = 0.0
    pass_all: float = 0.0
    valid_json_rate: float = 0.0
    validator_pass_rate: float = 0.0
    count_match_rate: float = 0.0
    br_coverage_mean: float = 0.0
    br_coverage_min: float = 0.0
    invented_br_total: int = 0
    invented_url_total: int = 0
    mean_attempts: float = 0.0
    mean_latency_ms: float = 0.0
    tc_jaccard_mean: float | None = None
    identical_runs: int = 0
    unique_artifacts: int = 0


class EnvironmentInfo(_StrictModel):
    base_url: str = ""
    ollama_version: str = ""
    model: str = ""
    digest: str | None = None
    temperature: float = 0.7
    seed: int | None = 42
    num_ctx: int = 4096
    num_predict: int = 512
    max_retries: int = 3


class InputInfo(_StrictModel):
    path: str = ""
    source_sha256: str = ""
    text_sha256: str = ""
    encoding: str = "utf-8"
    title: str = ""
    url: str | None = None
    br_ids: list[str] = Field(default_factory=list)
    normalizer: dict[str, Any] = Field(default_factory=dict)


class PromptInfo(_StrictModel):
    artifact_type: str = "testcases"
    count: int = 5
    presets: list[str] = Field(default_factory=list)
    prompt_sha256: str = ""


class VerdictInfo(_StrictModel):
    status: str = "fail"
    reasons: list[str] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)


class CellReport(_StrictModel):
    model: str
    digest: str | None = None
    params: RunParams = Field(default_factory=RunParams)
    summary: dict[str, Any] = Field(default_factory=dict)


class ExperimentReport(_StrictModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    kind: str
    started_at: str
    finished_at: str
    environment: EnvironmentInfo = Field(default_factory=EnvironmentInfo)
    input: InputInfo = Field(default_factory=InputInfo)
    prompt: PromptInfo = Field(default_factory=PromptInfo)
    params: dict[str, Any] = Field(default_factory=dict)
    runs: list[RunRecord] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    verdict: VerdictInfo = Field(default_factory=VerdictInfo)
    warnings: list[str] = Field(default_factory=list)
    baseline: CellReport | None = None
    candidate: CellReport | None = None
    comparison: dict[str, Any] | None = None


class ErrorInfo(_StrictModel):
    code: int
    message: str
    hint: str | None = None


class ErrorReport(_StrictModel):
    schema_version: int = SCHEMA_VERSION
    ok: bool = False
    kind: str = "error"
    error: ErrorInfo
