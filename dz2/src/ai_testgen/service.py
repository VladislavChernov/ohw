"""Generation service shared by the CLI and the Streamlit workbench.

Both fronts must run the same code path: build the prompt from the external
template, call Ollama, validate the answer, retry with feedback, then render
markdown. Keeping this in one place prevents the CLI and the UI from drifting.
"""

from collections.abc import Callable
from dataclasses import dataclass

from ai_testgen.handlers import ArtifactHandler
from ai_testgen.input_doc import RequirementDoc
from ai_testgen.models import ArtifactType, GenerationResult
from ai_testgen.ollama_client import OllamaClient
from ai_testgen.renderer import render, render_document


@dataclass(frozen=True)
class GenerationOptions:
    count: int = 10
    temperature: float = 0.7
    max_retries: int = 3


@dataclass(frozen=True)
class RetryNotice:
    """A validation failure that triggers another attempt."""

    attempt: int
    total_attempts: int
    issues: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedArtifact:
    artifact_type: ArtifactType
    result: GenerationResult
    user_prompt: str
    attempts: int

    def render(self, url: str | None, source_name: str) -> str:
        if self.result.document is not None:
            return render_document(
                self.artifact_type, url, source_name, self.result.document
            )
        return render(self.result.cases, url, source_name)


def testcase_index(result: GenerationResult) -> str:
    """Compact ``TC-01 (positive, BR-1): title`` lines for traceability prompts.

    The coverage matrix may only reference test cases that exist, so the CLI and
    the workbench hand it this list instead of letting the model guess ids.
    """
    lines = []
    for case in result.cases:
        requirement = case.requirement or "no requirement"
        lines.append(
            f"- {case.id} ({case.type.value}, {requirement}): {case.title}"
        )
    return "\n".join(lines)


class GenerationError(Exception):
    """The model could not produce a valid artifact within the retry budget."""

    def __init__(
        self,
        artifact_type: ArtifactType,
        issues: list[str],
        attempts: int,
    ) -> None:
        self.artifact_type = artifact_type
        self.issues = issues
        self.attempts = attempts
        super().__init__(
            f"could not produce valid {artifact_type.value} "
            f"after {attempts} attempt(s)"
        )


def generate_artifact(
    client: OllamaClient,
    handler: ArtifactHandler,
    doc: RequirementDoc,
    options: GenerationOptions,
    *,
    on_retry: Callable[[RetryNotice], None] | None = None,
    user_prompt: str | None = None,
) -> GeneratedArtifact:
    """Generate and validate one artifact for ``doc``.

    ``user_prompt`` lets the workbench send an edited prompt; when it is
    omitted the prompt is rendered from the external template.
    """
    system = handler.build_system()
    prompt = user_prompt or handler.build_user(doc, options.count)
    total_attempts = options.max_retries + 1
    use_feedback = False
    issues: list[str] = []

    for attempt in range(total_attempts):
        current = handler.build_retry(issues) if use_feedback else prompt
        raw = client.chat(
            system,
            current,
            options.temperature,
            json_mode=handler.wants_json,
        )
        result = handler.validate_response(raw, options.count)
        if result.ok:
            return GeneratedArtifact(
                artifact_type=handler.artifact_type,
                result=result,
                user_prompt=prompt,
                attempts=attempt + 1,
            )
        issues = list(result.issues)
        use_feedback = True
        if on_retry is not None:
            on_retry(RetryNotice(attempt + 1, total_attempts, tuple(issues)))

    raise GenerationError(handler.artifact_type, issues, total_attempts)
