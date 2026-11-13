"""Generation phase: requirement files in, rendered artifacts and metrics out."""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from src.config import DEFAULT_QUALITY_WEIGHTS, validate_quality_weights
from src.generator.handlers import ArtifactHandler, get_handler
from src.generator.input_doc import RequirementDoc, load_docs
from src.generator.models import ArtifactType
from src.generator.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from src.generator.presets import (
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
from src.models import ScenarioResult
from src.quality.assessor import (
    assess_quality,
    covered_requirement_ids,
    extract_requirement_ids,
)


@dataclass(frozen=True)
class GenerationRequest:
    """Everything the generation phase needs, independent from argparse."""

    input_dir: Path
    artifact_type: ArtifactType
    count: int = 10
    runs: int = 1
    temperature: float = 0.7
    max_retries: int = 3
    url: str | None = None
    normalize: bool = False
    presets: Sequence[str] = ()
    prompt_dir: Path | None = None
    output_dir: Path = Path("output")
    # Per-file prompt overrides from the workbench; the CLI leaves this empty
    # and prompts are rendered from the templates.
    prompts: Mapping[str, str] = field(default_factory=dict)
    # Record a connection failure per file instead of aborting the whole run.
    continue_on_error: bool = False
    # Empty means the project defaults; the resolved weights are stored in the
    # snapshot so the assessment phase reuses them.
    quality_weights: Mapping[str, float] = field(default_factory=dict)


@dataclass
class GenerationOutcome:
    scenarios: list[ScenarioResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    quality_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_QUALITY_WEIGHTS)
    )


def _requirement_ids(doc: RequirementDoc) -> list[str]:
    if doc.normalization is not None and doc.normalization.requirements:
        return [requirement.id for requirement in doc.normalization.requirements]
    return extract_requirement_ids(doc.content)


def _render(artifact: GeneratedArtifact, doc: RequirementDoc) -> str:
    return artifact.render(doc.url, doc.path.stem)


def _extra_prompt(
    handler: ArtifactHandler,
    doc: RequirementDoc,
    count: int,
    extras: Sequence[str],
    testcases: str | None = None,
) -> str | None:
    if not extras and testcases is None:
        return None
    return handler.build_user(doc, count, extras, testcases)


def generate_scenarios(
    request: GenerationRequest,
    client: OllamaClient,
    *,
    on_retry: Callable[[RetryNotice], None] | None = None,
) -> GenerationOutcome:
    """Generate one artifact per input file and attach quality metrics.

    A connection failure aborts the run by default: every later request would
    fail too. With ``continue_on_error`` the failure is recorded for the current
    file and the remaining files are still processed. A single model failure
    (invalid answer after the retry budget) is recorded and the remaining files
    are still processed.
    """
    if request.runs < 1:
        raise ValueError("runs must be at least 1")

    weights = (
        validate_quality_weights(request.quality_weights)
        if request.quality_weights
        else dict(DEFAULT_QUALITY_WEIGHTS)
    )

    docs = load_docs(
        request.input_dir,
        request.url,
        normalize=request.normalize,
    )
    handler = get_handler(request.artifact_type, request.prompt_dir)
    testcases_handler = None
    if request.artifact_type is ArtifactType.COVERAGEMATRIX:
        # The matrix may only reference real ids: generate test cases first and
        # feed their index into the ``{testcases}`` placeholder.
        testcases_handler = get_handler(ArtifactType.TESTCASES, request.prompt_dir)
    options = GenerationOptions(
        count=request.count,
        temperature=request.temperature,
        max_retries=request.max_retries,
    )
    extras: tuple[str, ...] = ()
    if request.presets:
        presets_dir = resolve_presets_dir()
        if presets_dir is None:
            raise PresetError("no presets directory found")
        extras = tuple(
            select_extras(
                load_presets(presets_dir),
                request.presets,
                request.artifact_type,
            )
        )

    scenarios_dir = request.output_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    outcome = GenerationOutcome(quality_weights=weights)
    for doc in docs:
        try:
            user_prompt = _extra_prompt(handler, doc, request.count, extras)
            if testcases_handler is not None:
                testcases_artifact = generate_artifact(
                    client,
                    testcases_handler,
                    doc,
                    options,
                    on_retry=on_retry,
                )
                user_prompt = _extra_prompt(
                    handler,
                    doc,
                    request.count,
                    extras,
                    testcase_index(testcases_artifact.result),
                )
            override = request.prompts.get(doc.path.name)
            if override:
                user_prompt = override
            artifact = generate_artifact(
                client,
                handler,
                doc,
                options,
                on_retry=on_retry,
                user_prompt=user_prompt,
            )
            rendered = _render(artifact, doc)
            generations = [rendered]
            for _ in range(request.runs - 1):
                repeat = generate_artifact(
                    client,
                    handler,
                    doc,
                    options,
                    on_retry=on_retry,
                    user_prompt=user_prompt,
                )
                generations.append(_render(repeat, doc))

            requirement_ids = _requirement_ids(doc)
            quality = assess_quality(
                rendered,
                requirement_ids,
                generations if len(generations) > 1 else None,
                weights,
            )
            output_path = scenarios_dir / f"{doc.path.stem}.md"
            output_path.write_text(rendered, encoding="utf-8")
            outcome.scenarios.append(
                ScenarioResult(
                    filename=doc.path.name,
                    source_path=str(doc.path),
                    artifact_type=request.artifact_type.value,
                    output_file=str(output_path),
                    rendered=rendered,
                    cases=list(artifact.result.cases),
                    prompt=artifact.user_prompt,
                    attempts=artifact.attempts,
                    quality=quality,
                    requirement_ids=requirement_ids,
                    covered_requirements=covered_requirement_ids(
                        rendered, requirement_ids
                    ),
                    prompt_sha256=hashlib.sha256(
                        artifact.user_prompt.encode("utf-8")
                    ).hexdigest(),
                    runs=len(generations),
                    generations=generations,
                )
            )
        except (GenerationError, OllamaResponseError) as exc:
            outcome.errors.append(f"{doc.path.name}: {exc}")
        except OllamaConnectionError as exc:
            if not request.continue_on_error:
                raise
            outcome.errors.append(f"{doc.path.name}: Ollama недоступна: {exc}")
            continue
    return outcome
