import math
import re

_BR_ID_PATTERN = re.compile(r"\bBR-\d+\b")

_JSON_SCHEMA_EXAMPLE = (
    '{"cases": [{"id": "TC-01", "type": "positive", "title": "...", '
    '"preconditions": ["..."], "steps": ["step 1", "step 2"], "expected": "...", '
    '"requirement": "BR-1"}]}'
)


def target_negative_count(count: int) -> int:
    if count < 2:
        return 0
    return max(1, min(count - 1, math.ceil(count * 0.4)))


def build_system_prompt() -> str:
    return (
        "You are a senior QA engineer. You generate web site test cases "
        "as strict JSON. Respond with a single JSON object only, no markdown "
        f"fences, no extra text. The object must match this schema exactly: {_JSON_SCHEMA_EXAMPLE} "
        "The 'cases' key holds the array of ALL generated test cases. Each 'type' must be "
        "either 'positive' or 'negative'. Each 'steps' must contain at least one concrete "
        "action. Each 'expected' must describe the verifiable result. Each 'requirement' "
        "must reference the business requirement id (e.g. BR-1) from the requirements text "
        "that this case verifies. Write ALL titles, "
        "preconditions, steps and expected results in Russian. Keep product names, "
        "UI element labels and URLs in their original language."
    )


def build_user_prompt(content: str, url: str | None, count: int) -> str:
    negative = target_negative_count(count)
    positive = count - negative
    requirements = (
        f"Generate exactly {count} test cases: {positive} positive and {negative} negative."
        if negative > 0
        else f"Generate exactly {count} test case (positive)."
    )
    site = f"Target site: {url}\n\n" if url else ""
    requirement_rule = (
        "Every test case MUST set its 'requirement' field to the business "
        "requirement id (BR-N) from the Requirements section that it verifies.\n\n"
        if _BR_ID_PATTERN.search(content)
        else "\n"
    )
    return (
        f"{site}{requirements}\n"
        f'Respond with a JSON object {{"cases": [...]}} containing all {count} test cases.\n'
        f"{requirement_rule}"
        f"Requirements:\n{content}"
    )


def build_retry_prompt(issues: list[str]) -> str:
    problems = "\n".join(f"- {issue}" for issue in issues)
    return (
        "Your previous response violated the constraints:\n"
        f"{problems}\n"
        "Fix the problems and respond again with the full corrected JSON array only."
    )


TESTPLAN_SECTIONS: tuple[str, ...] = (
    "Цели тестирования",
    "Объём тестирования",
    "Подход и виды тестирования",
    "Критерии начала и окончания тестирования",
    "Риски",
)


def build_doc_system_prompt() -> str:
    return (
        "You are a senior QA engineer. You write documents as clean GitHub-flavored "
        "Markdown in Russian. Keep product names, UI element labels and URLs in "
        "their original language. Respond with "
        "the document text only: no code fences, no JSON, no commentary before or "
        "after. Structure the document with '## ' section headings exactly as "
        "required by the task."
    )


def build_checklist_user_prompt(content: str, url: str | None) -> str:
    site = f"Target site: {url}\n\n" if url else ""
    return (
        f"{site}Create a test checklist as a Markdown document.\n"
        "Rules:\n"
        "- Each checklist zone is an '## ' heading; use the zone names from the "
        "requirements below.\n"
        "- Under every zone heading put at least one concrete check as a '- ' list "
        "item.\n"
        "- End every check item with the business requirement id it verifies in "
        "parentheses, e.g. '(BR-1)'.\n"
        "- Do not wrap the document in code fences and do not respond with JSON.\n\n"
        f"Requirements:\n{content}"
    )


def build_testplan_user_prompt(content: str, url: str | None) -> str:
    site = f"Target site: {url}\n\n" if url else ""
    sections = "\n".join(f"- ## {name}" for name in TESTPLAN_SECTIONS)
    return (
        f"{site}Write a test plan as a Markdown document.\n"
        "The document must contain exactly these '## ' section headings, in this "
        f"order:\n{sections}\n"
        "Every section must contain at least one sentence of real content; if there "
        "are no risks, write why. In the scope section list the covered business "
        "requirement ids (BR-1, BR-2, ...) explicitly. Do not wrap the document in "
        "code fences and do not respond with JSON.\n\n"
        f"Requirements:\n{content}"
    )


def build_markdown_retry_prompt(issues: list[str]) -> str:
    problems = "\n".join(f"- {issue}" for issue in issues)
    return (
        "Your previous response violated the constraints:\n"
        f"{problems}\n"
        "Fix the problems and respond again with the full corrected Markdown "
        "document only."
    )
