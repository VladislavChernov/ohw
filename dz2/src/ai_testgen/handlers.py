from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ai_testgen.input_doc import RequirementDoc
from ai_testgen.models import ArtifactType, GenerationResult
from ai_testgen.prompt import (
    COVERAGE_MATRIX_SECTIONS,
    QAREPORT_SECTIONS,
    TESTPLAN_SECTIONS,
    build_doc_system_prompt,
    build_markdown_retry_prompt,
    build_retry_prompt,
    build_system_prompt,
    build_user_prompt,
    load_prompt_template,
)
from ai_testgen.validator import validate, validate_markdown


class UserPromptBuilder(Protocol):
    """Builds the user prompt; presets and test cases are optional."""

    def __call__(
        self,
        doc: RequirementDoc,
        count: int,
        extras: Sequence[str] = (),
        testcases: str | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class ArtifactHandler:
    artifact_type: ArtifactType
    wants_json: bool
    build_system: Callable[[], str]
    build_user: UserPromptBuilder
    validate_response: Callable[[str, int], GenerationResult]
    build_retry: Callable[[list[str]], str]


def _user_builder(
    artifact_type: ArtifactType, template: str
) -> UserPromptBuilder:
    def build(
        doc: RequirementDoc,
        count: int,
        extras: Sequence[str] = (),
        testcases: str | None = None,
    ) -> str:
        return build_user_prompt(
            artifact_type,
            template,
            doc.content,
            doc.url,
            count,
            extras,
            testcases,
        )

    return build


def _testcases_validate(raw: str, count: int) -> GenerationResult:
    result = validate(raw, count)
    return GenerationResult(cases=result.cases, issues=result.issues)


def _checklist_validate(raw: str, count: int) -> GenerationResult:
    return validate_markdown(raw, items_under_every_section=True)


def _testplan_validate(raw: str, count: int) -> GenerationResult:
    return validate_markdown(raw, required_sections=TESTPLAN_SECTIONS)


def _qareport_validate(raw: str, count: int) -> GenerationResult:
    return validate_markdown(raw, required_sections=QAREPORT_SECTIONS)


def _coveragematrix_validate(raw: str, count: int) -> GenerationResult:
    return validate_markdown(
        raw,
        required_sections=COVERAGE_MATRIX_SECTIONS,
        require_table=True,
    )


def get_handler(
    artifact_type: ArtifactType,
    prompt_dir: Path | None = None,
) -> ArtifactHandler:
    """Build a handler whose user prompt comes from ``<prompt_dir>/<type>.txt``."""
    template = load_prompt_template(artifact_type, prompt_dir)

    if artifact_type is ArtifactType.TESTCASES:
        return ArtifactHandler(
            artifact_type=artifact_type,
            wants_json=True,
            build_system=build_system_prompt,
            build_user=_user_builder(artifact_type, template),
            validate_response=_testcases_validate,
            build_retry=build_retry_prompt,
        )

    validators: dict[ArtifactType, Callable[[str, int], GenerationResult]] = {
        ArtifactType.CHECKLIST: _checklist_validate,
        ArtifactType.TESTPLAN: _testplan_validate,
        ArtifactType.QAREPORT: _qareport_validate,
        ArtifactType.COVERAGEMATRIX: _coveragematrix_validate,
    }
    return ArtifactHandler(
        artifact_type=artifact_type,
        wants_json=False,
        build_system=build_doc_system_prompt,
        build_user=_user_builder(artifact_type, template),
        validate_response=validators[artifact_type],
        build_retry=build_markdown_retry_prompt,
    )
