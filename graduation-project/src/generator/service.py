#!/usr/bin/env python3
"""Generator module extending DZ2 service with quality metrics."""

from dataclasses import dataclass
from typing import List, Optional
from ai_testgen.service import GeneratedArtifact, GenerationResult, ArtifactType


@dataclass(frozen=True)
class QualityMetrics:
    """NLP metrics for generated test documentation."""
    readability: float = 0.0
    clarity_goals: float = 0.0
    scenario_coverage: float = 0.0
    consistency: float = 0.0
    overall: float = 0.0


@dataclass(frozen=True)
class EnhancedGeneratedArtifact:
    """Extended artifact with quality metrics."""
    artifact_type: ArtifactType
    result: GenerationResult
    user_prompt: str
    attempts: int
    quality_metrics: QualityMetrics = QualityMetrics()
    cases_with_requirements: List[str] = None