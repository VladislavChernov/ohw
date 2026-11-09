import math
import os
import re
from collections.abc import Sequence
from pathlib import Path

from ai_testgen.models import ArtifactType

PROMPT_DIR_NAME = "prompts"
PROMPT_DIR_ENV = "DZ2_PROMPT_DIR"
SYSTEM_DIR_NAME = "_system"
# Editable install / repository checkout: <repo>/dz2/prompts
DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / PROMPT_DIR_NAME
# Package data fallback (non-editable install with prompts copied next to the module).
PACKAGE_PROMPT_DIR = Path(__file__).resolve().parent / PROMPT_DIR_NAME

_PLACEHOLDER_PATTERN = re.compile(r"\{([a-z][a-z0-9_]*)\}", re.IGNORECASE)
_URL_LINE_PATTERN = re.compile(r"^Target site: \{url\}[ \t]*\n?", re.MULTILINE)
_BR_ID_PATTERN = re.compile(r"\bBR-\d+\b")

TESTPLAN_SECTIONS: tuple[str, ...] = (
    "Цели тестирования",
    "Объём тестирования",
    "Подход и виды тестирования",
    "Критерии начала и окончания тестирования",
    "Риски",
)

QAREPORT_SECTIONS: tuple[str, ...] = (
    "Введение",
    "Тестовые сценарии",
    "Дефекты",
    "Рекомендации",
)

COVERAGE_MATRIX_SECTIONS: tuple[str, ...] = ("Матрица покрытия требований",)


class PromptError(Exception):
    """Prompt template is missing or malformed."""


def resolve_prompt_dir(explicit: Path | None = None) -> Path:
    """Resolve the directory with per-artifact prompt templates.

    Order: explicit ``--prompt-dir``, ``DZ2_PROMPT_DIR``, ``./prompts``,
    repository ``prompts``, package ``prompts``. Explicit values are strict:
    a wrong path is an error instead of a silent fallback.
    """
    if explicit is not None:
        return _require_dir(explicit, "--prompt-dir")

    env_value = os.environ.get(PROMPT_DIR_ENV)
    if env_value:
        return _require_dir(Path(env_value), PROMPT_DIR_ENV)

    for candidate in (
        Path.cwd() / PROMPT_DIR_NAME,
        DEFAULT_PROMPT_DIR,
        PACKAGE_PROMPT_DIR,
    ):
        if candidate.is_dir():
            return candidate

    raise PromptError(
        f"prompt directory not found: tried ./{PROMPT_DIR_NAME}, "
        f"{DEFAULT_PROMPT_DIR}, {PACKAGE_PROMPT_DIR}; "
        f"pass --prompt-dir or set {PROMPT_DIR_ENV}"
    )


def _require_dir(path: Path, source: str) -> Path:
    if not path.is_dir():
        raise PromptError(f"prompt directory from {source} does not exist: {path}")
    return path


def resolve_system_prompt_dir(prompt_dir: Path | None = None) -> Path:
    """Resolve the directory with system and retry prompt templates.

    ``<prompt_dir>/_system`` wins so custom prompt directories can override the
    system prompts together with the artifact templates. Directories that only
    contain per-artifact templates keep using the repository defaults.
    """
    resolved = resolve_prompt_dir(prompt_dir)
    candidates = (
        resolved / SYSTEM_DIR_NAME,
        DEFAULT_PROMPT_DIR / SYSTEM_DIR_NAME,
        PACKAGE_PROMPT_DIR / SYSTEM_DIR_NAME,
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise PromptError(
        "system prompt directory not found: tried "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def load_system_template(name: str, prompt_dir: Path | None = None) -> str:
    """Read ``<system_dir>/<name>`` and drop the trailing newline."""
    path = resolve_system_prompt_dir(prompt_dir) / name
    if not path.is_file():
        raise PromptError(f"system prompt template not found: {path}")
    try:
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise PromptError(f"cannot read system prompt template {path}: {exc}") from exc


def load_prompt_template(
    artifact_type: ArtifactType,
    prompt_dir: Path | None = None,
) -> str:
    """Read ``<prompt_dir>/<artifact_type>.txt``."""
    directory = resolve_prompt_dir(prompt_dir)
    path = directory / f"{artifact_type.value}.txt"
    if not path.is_file():
        supported = ", ".join(member.value for member in ArtifactType)
        raise PromptError(
            f"prompt template not found: {path} "
            f"(expected one file per artifact type: {supported})"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"cannot read prompt template {path}: {exc}") from exc


def strip_template_comments(template: str) -> str:
    """Drop the leading ``#`` comment block and blank lines from a template."""
    lines = template.splitlines()
    index = 0
    while index < len(lines) and (
        not lines[index].strip() or lines[index].lstrip().startswith("#")
    ):
        index += 1
    return "\n".join(lines[index:])


def render_template(template: str, values: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders without touching literal JSON braces."""

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1).lower(), match.group(0))

    return _PLACEHOLDER_PATTERN.sub(replace, template)


def build_user_prompt(
    artifact_type: ArtifactType,
    template: str,
    content: str,
    url: str | None,
    count: int,
    extras: Sequence[str] = (),
    testcases: str | None = None,
) -> str:
    """Render a user prompt from an external template.

    ``testcases`` carries a plain list of already generated test cases so the
    coverage matrix can reference real ids instead of inventing them.
    """
    negative = target_negative_count(count)
    positive = count - negative

    values = {
        "content": content,
        "url": url or "",
        "count": str(count),
        "count_noun": "test case" if count == 1 else "test cases",
        "positive": str(positive),
        "negative": str(negative),
        "requirement_rule": _requirement_rule(content),
        "extra": _extra_block(extras),
        "testcases": _testcases_block(testcases),
    }

    text = strip_template_comments(template)
    if extras and "{extra}" not in text:
        raise PromptError(
            f"template {artifact_type.value}.txt has no {{extra}} placeholder, "
            "so presets cannot be applied"
        )
    if url is None:
        text = _URL_LINE_PATTERN.sub("", text)

    prompt = render_template(text, values)
    leftover = sorted(set(_PLACEHOLDER_PATTERN.findall(prompt)))
    if leftover:
        raise PromptError(
            f"unknown placeholder(s) in {artifact_type.value}.txt: "
            + ", ".join(f"{{{name}}}" for name in leftover)
        )

    return re.sub(r"\n{3,}", "\n\n", prompt).strip() + "\n"


def _extra_block(extras: Sequence[str]) -> str:
    """Format preset instructions; empty string keeps the prompt unchanged."""
    if not extras:
        return ""
    lines = [
        "Additional instructions:",
        "- Apply them only to the requirements below; do not add pages, features,",
        "  products or data the requirements do not mention, and do not change",
        "  the required output format.",
    ]
    lines += [f"- {' '.join(text.split())}" for text in extras]
    return "\n".join(lines)


def _requirement_rule(content: str) -> str:
    if not _BR_ID_PATTERN.search(content):
        return ""
    return (
        "Every test case MUST set its 'requirement' field to the business "
        "requirement id (BR-N) from the Requirements section that it verifies."
    )


def _testcases_block(testcases: str | None) -> str:
    """State plainly whether a test case list is available.

    The coverage matrix may only reuse ids it was given, so an empty list is
    spelled out instead of leaving a dangling section.
    """
    text = (testcases or "").strip()
    if not text:
        return "No test cases have been generated for these requirements yet."
    return "Test cases already generated for these requirements:\n" + text


def target_negative_count(count: int) -> int:
    if count < 2:
        return 0
    return max(1, min(count - 1, math.ceil(count * 0.4)))


def build_system_prompt(prompt_dir: Path | None = None) -> str:
    schema = (
        '{"cases": [{"id": "TC-01", "type": "positive", "title": "...", '
        '"preconditions": ["..."], "steps": ["step 1", "step 2"], "expected": "...", '
        '"requirement": "BR-1"}]}'
    )
    template = load_system_template("system-json.txt", prompt_dir)
    return render_template(template, {"schema": schema})


def build_doc_system_prompt(prompt_dir: Path | None = None) -> str:
    return load_system_template("system-markdown.txt", prompt_dir)


def build_retry_prompt(issues: list[str], prompt_dir: Path | None = None) -> str:
    problems = "\n".join(f"- {issue}" for issue in issues)
    template = load_system_template("retry-json.txt", prompt_dir)
    return render_template(template, {"problems": problems})


def build_markdown_retry_prompt(issues: list[str], prompt_dir: Path | None = None) -> str:
    problems = "\n".join(f"- {issue}" for issue in issues)
    template = load_system_template("retry-markdown.txt", prompt_dir)
    return render_template(template, {"problems": problems})
