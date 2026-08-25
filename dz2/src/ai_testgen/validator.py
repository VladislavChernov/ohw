import json
import re

from pydantic import ValidationError

from ai_testgen.models import CaseType, GenerationResult, TestCase, ValidationResult

_CASE_KEYS = {"id", "type", "steps", "expected"}

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_NUMBERING_PATTERN = re.compile(r"^\d+[.)]\s+")


def _unwrap_cases(data: object) -> tuple[list[object], str | None]:
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        cases = data.get("cases")
        if isinstance(cases, list):
            return cases, None
        if _CASE_KEYS.issubset(data.keys()):
            return [data], None
    return [], "response must be a JSON array of test cases or {\"cases\": [...]}"


def validate(raw: str, expected_count: int) -> ValidationResult:
    issues: list[str] = []
    cases: list[TestCase] = []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ValidationResult(
            cases=[], issues=[f"response is not valid JSON: {exc.msg} (line {exc.lineno})"]
        )

    items, unwrap_issue = _unwrap_cases(data)
    if unwrap_issue is not None:
        return ValidationResult(cases=[], issues=[unwrap_issue])

    for index, item in enumerate(items):
        try:
            cases.append(TestCase.model_validate(item))
        except ValidationError as exc:
            issues.append(f"test case #{index + 1} is invalid: {_format_validation_error(exc)}")

    if not issues:
        issues.extend(_check_set_constraints(cases, expected_count))

    return ValidationResult(cases=cases, issues=issues)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)


def validate_markdown(
    raw: str,
    required_sections: tuple[str, ...] = (),
    items_under_every_section: bool = False,
) -> GenerationResult:
    document = _strip_code_fences(raw)

    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(parsed, (dict, list)):
            return GenerationResult(
                issues=["response is valid JSON but a Markdown document is expected"]
            )

    lines = document.splitlines()
    h2_headings = [
        (match.group(2).strip(), index)
        for index, line in enumerate(lines)
        if (match := _HEADING_PATTERN.match(line)) is not None and len(match.group(1)) == 2
    ]
    if not h2_headings:
        return GenerationResult(issues=["no '##' section headings found"])

    def section_body(start_index: int) -> list[str]:
        next_indices = [index for _, index in h2_headings if index > start_index]
        end = min(next_indices) if next_indices else len(lines)
        return lines[start_index + 1 : end]

    by_title = {title.casefold(): (title, section_body(index)) for title, index in h2_headings}

    issues: list[str] = []
    for required in required_sections:
        entry = _find_section(by_title, required)
        if entry is None:
            issues.append(f"missing required section: {required}")
        elif not "\n".join(entry).strip():
            issues.append(f"section has no content: {required}")

    if items_under_every_section:
        for title, index in h2_headings:
            if not any(_LIST_ITEM_PATTERN.match(line) for line in section_body(index)):
                issues.append(f"zone has no checklist items: {title}")

    return GenerationResult(document=document, issues=issues)


def _find_section(
    by_title: dict[str, tuple[str, list[str]]], required: str
) -> list[str] | None:
    needle = required.casefold()
    for title, body in by_title.values():
        normalized = _NUMBERING_PATTERN.sub("", title).casefold()
        if normalized == needle or needle in normalized:
            return body
    return None


def _strip_code_fences(raw: str) -> str:
    stripped = raw.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    lines = lines[1:]
    if lines and lines[-1].strip().endswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _check_set_constraints(cases: list[TestCase], expected_count: int) -> list[str]:
    issues: list[str] = []
    total = len(cases)

    if total != expected_count:
        issues.append(f"expected exactly {expected_count} test cases, got {total}")

    positives = sum(1 for case in cases if case.type == CaseType.POSITIVE)
    negatives = total - positives

    if expected_count >= 2 and total > 0:
        if negatives < 1:
            issues.append("no negative test cases: at least one negative scenario is required")
        elif positives < 1:
            issues.append("no positive test cases: at least one positive scenario is required")

    if expected_count >= 5 and negatives * 3 < expected_count:
        issues.append(
            f"only {negatives} negative test cases out of {total}: "
            f"at least a third of {expected_count} is required"
        )

    return issues
