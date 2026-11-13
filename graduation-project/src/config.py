"""Runtime settings for the standalone project."""

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
