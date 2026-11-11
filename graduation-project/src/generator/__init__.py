"""Generation core vendored from Dz2.

The modules keep the original behaviour (prompt templates, normalizer,
validation, retries, rendering) but live under ``src.generator`` so the project
can be copied and run on its own.
"""

from src.generator.input_doc import (
    InputError,
    RequirementDoc,
    collect_input_files,
    load_docs,
)
from src.generator.models import (
    ArtifactType,
    CaseType,
    GenerationResult,
    TestCase,
    ValidationResult,
)
from src.generator.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from src.generator.presets import (
    Preset,
    PresetError,
    load_presets,
    resolve_presets_dir,
    select_extras,
)
from src.generator.service import (
    GeneratedArtifact,
    GenerationError,
    GenerationOptions,
    RetryNotice,
    generate_artifact,
    testcase_index,
)

__all__ = [
    "ArtifactType",
    "CaseType",
    "GeneratedArtifact",
    "GenerationError",
    "GenerationOptions",
    "GenerationResult",
    "InputError",
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaResponseError",
    "Preset",
    "PresetError",
    "RequirementDoc",
    "RetryNotice",
    "TestCase",
    "ValidationResult",
    "collect_input_files",
    "generate_artifact",
    "generate_scenarios",
    "load_docs",
    "load_presets",
    "resolve_presets_dir",
    "select_extras",
    "testcase_index",
]
