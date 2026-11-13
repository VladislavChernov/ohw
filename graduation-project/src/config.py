"""Runtime settings for the standalone project."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 600.0
DEFAULT_OUTPUT_DIR = Path("output")

# Contract version of ``generation.json`` and ``quality-metrics.json``. Bump it
# whenever a later pipeline stage would misread artifacts from an older run.
PIPELINE_VERSION = "0.1.0"

METRIC_KEYS: tuple[str, ...] = (
    "readability",
    "clarity_goals",
    "scenario_coverage",
    "consistency",
)

QUALITY_WEIGHTS_ENV = "GRADUATION_QUALITY_WEIGHTS"

DEFAULT_QUALITY_WEIGHTS: dict[str, float] = {
    "readability": 0.25,
    "clarity_goals": 0.25,
    "scenario_coverage": 0.30,
    "consistency": 0.20,
}

WEIGHTS_SUM_TOLERANCE = 1e-6


def _format_weight(value: float) -> str:
    """Prefer two decimals, but do not hide extra precision."""
    rounded = f"{value:.2f}"
    if math.isclose(float(rounded), value, abs_tol=1e-9):
        return rounded
    return f"{value:g}"


def format_quality_weights(weights: Mapping[str, float]) -> str:
    """Render weights as the canonical ``metric=value`` list."""
    return ", ".join(
        f"{key}={_format_weight(weights[key])}" for key in METRIC_KEYS
    )


def validate_quality_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    """Return valid weights in canonical order or raise ``ValueError``."""
    unknown = [key for key in weights if key not in METRIC_KEYS]
    if unknown:
        raise ValueError(
            f"неизвестные метрики весов: {', '.join(sorted(unknown))}; "
            f"допустимые: {', '.join(METRIC_KEYS)}"
        )
    missing = [key for key in METRIC_KEYS if key not in weights]
    if missing:
        raise ValueError(
            f"не заданы веса метрик: {', '.join(missing)}; "
            f"нужны все: {', '.join(METRIC_KEYS)}"
        )

    resolved: dict[str, float] = {}
    for key in METRIC_KEYS:
        value = weights[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"вес метрики {key!r} должен быть числом: {value!r}")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"вес метрики {key!r} должен быть конечным: {numeric}")
        if numeric < 0:
            raise ValueError(f"вес метрики {key!r} не может быть отрицательным")
        resolved[key] = numeric

    total = sum(resolved.values())
    if not math.isclose(total, 1.0, abs_tol=WEIGHTS_SUM_TOLERANCE):
        raise ValueError(
            f"сумма весов overall должна быть 1.0, получено {total:g}"
        )
    return resolved


def parse_quality_weights(raw: str) -> dict[str, float]:
    """Parse ``metric=value`` pairs separated by commas."""
    text = raw.strip()
    if not text:
        raise ValueError("веса overall пусты; ожидается metric=value,...")

    parsed: dict[str, float] = {}
    for item in text.split(","):
        name, separator, raw_value = item.partition("=")
        key = name.strip()
        if not separator or not key:
            raise ValueError(
                f"неверный элемент весов: {item.strip()!r}; ожидается metric=value"
            )
        if key in parsed:
            raise ValueError(f"метрика {key!r} указана дважды")
        try:
            parsed[key] = float(raw_value)
        except ValueError:
            raise ValueError(
                f"вес метрики {key!r} не является числом: {raw_value.strip()!r}"
            ) from None
    return validate_quality_weights(parsed)


def resolve_quality_weights(
    cli_value: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, float]:
    """Resolve weights with CLI > env > defaults precedence."""
    source = cli_value
    if source is None:
        source = (os.environ if env is None else env).get(QUALITY_WEIGHTS_ENV)
    if source is None or not source.strip():
        return dict(DEFAULT_QUALITY_WEIGHTS)
    return parse_quality_weights(source)


def check_pipeline_version(raw: Mapping[str, Any], path: Path) -> None:
    """Reject artifacts written by an incompatible pipeline version.

    Files without the key predate versioning. Their format is compatible with
    ``PIPELINE_VERSION``, so they are still readable as legacy artifacts.
    """
    version = raw.get("pipeline_version")
    if version is None:
        return
    if version != PIPELINE_VERSION:
        raise ValueError(
            f"несовместимая версия пайплайна: {path.name} записан версией "
            f"{version}, текущая версия — {PIPELINE_VERSION}; "
            f"перегенерируйте артефакты"
        )


@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    output_dir: Path = DEFAULT_OUTPUT_DIR

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        output_dir: str | Path | None = None,
    ) -> "Settings":
        """Build settings from explicit values, then environment, then defaults."""
        resolved_url = base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL
        resolved_model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL
        resolved_timeout = timeout
        if resolved_timeout is None:
            raw_timeout = os.environ.get("OLLAMA_TIMEOUT")
            resolved_timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
        resolved_output = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
        return cls(
            base_url=resolved_url.rstrip("/"),
            model=resolved_model,
            timeout=resolved_timeout,
            output_dir=resolved_output,
        )
