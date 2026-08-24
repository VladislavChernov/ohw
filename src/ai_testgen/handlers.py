from collections.abc import Callable
from dataclasses import dataclass

from ai_testgen.input_doc import RequirementDoc
from ai_testgen.models import ArtifactType, GenerationResult
from ai_testgen.prompt import (
    TESTPLAN_SECTIONS,
    build_checklist_user_prompt,
    build_doc_system_prompt,
    build_markdown_retry_prompt,
    build_retry_prompt,
    build_system_prompt,
    build_testplan_user_prompt,
    build_user_prompt,
)
from ai_testgen.validator import validate, validate_markdown


@dataclass(frozen=True)
class ArtifactHandler:
    artifact_type: ArtifactType
    wants_json: bool
    build_system: Callable[[], str]
    build_user: Callable[[RequirementDoc, int], str]
    validate_response: Callable[[str, int], GenerationResult]
    build_retry: Callable[[list[str]], str]


def _testcases_user(doc: RequirementDoc, count: int) -> str:
    return build_user_prompt(doc.content, doc.url, count)


def _testcases_validate(raw: str, count: int) -> GenerationResult:
    result = validate(raw, count)
    return GenerationResult(cases=result.cases, issues=result.issues)


def _checklist_validate(raw: str, count: int) -> GenerationResult:
    return validate_markdown(raw, items_under_every_section=True)


def _testplan_validate(raw: str, count: int) -> GenerationResult:
    return validate_markdown(raw, required_sections=TESTPLAN_SECTIONS)


def _doc_user(builder: Callable[[str, str | None], str]) -> Callable[[RequirementDoc, int], str]:
    def build(doc: RequirementDoc, count: int) -> str:
        return builder(doc.content, doc.url)

    return build


HANDLERS: dict[ArtifactType, ArtifactHandler] = {
    ArtifactType.TESTCASES: ArtifactHandler(
        artifact_type=ArtifactType.TESTCASES,
        wants_json=True,
        build_system=build_system_prompt,
        build_user=_testcases_user,
        validate_response=_testcases_validate,
        build_retry=build_retry_prompt,
    ),
    ArtifactType.CHECKLIST: ArtifactHandler(
        artifact_type=ArtifactType.CHECKLIST,
        wants_json=False,
        build_system=build_doc_system_prompt,
        build_user=_doc_user(build_checklist_user_prompt),
        validate_response=_checklist_validate,
        build_retry=build_markdown_retry_prompt,
    ),
    ArtifactType.TESTPLAN: ArtifactHandler(
        artifact_type=ArtifactType.TESTPLAN,
        wants_json=False,
        build_system=build_doc_system_prompt,
        build_user=_doc_user(build_testplan_user_prompt),
        validate_response=_testplan_validate,
        build_retry=build_markdown_retry_prompt,
    ),
}


def get_handler(artifact_type: ArtifactType) -> ArtifactHandler:
    return HANDLERS[artifact_type]