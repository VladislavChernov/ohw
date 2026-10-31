from pathlib import Path

import pytest

from ai_testgen.models import ArtifactType
from ai_testgen.prompt import (
    PromptError,
    build_user_prompt,
    load_prompt_template,
    render_template,
    resolve_prompt_dir,
    strip_template_comments,
)


def test_render_template_keeps_literal_json_braces() -> None:
    template = 'Respond with {"cases": [...]} and exactly {count} cases.'

    rendered = render_template(template, {"count": "5"})

    assert rendered == 'Respond with {"cases": [...]} and exactly 5 cases.'


def test_strip_template_comments_removes_leading_header() -> None:
    template = "# Comment one\n# Placeholders: {count}\n\nTarget site: {url}\n"

    assert strip_template_comments(template) == "Target site: {url}"


def test_build_user_prompt_substitutes_every_placeholder() -> None:
    template = (
        "Target site: {url}\n\n"
        "Generate {count}: {positive} positive, {negative} negative.\n\n"
        "{requirement_rule}\n\nRequirements:\n{content}"
    )

    prompt = build_user_prompt(
        ArtifactType.TESTCASES,
        template,
        "BR-1: login works",
        "https://example.com",
        5,
    )

    assert "Target site: https://example.com" in prompt
    assert "Generate 5: 3 positive, 2 negative." in prompt
    assert "BR-1: login works" in prompt
    assert "requirement id" in prompt


def test_testcases_block_reports_absence_of_test_cases() -> None:
    template = "{testcases}\n\nRequirements:\n{content}"

    prompt = build_user_prompt(
        ArtifactType.COVERAGEMATRIX, template, "BR-1: login", None, 5
    )

    assert "No test cases have been generated for these requirements yet." in prompt


def test_testcases_block_carries_ids_into_the_matrix_prompt() -> None:
    template = "{testcases}\n\nRequirements:\n{content}"

    prompt = build_user_prompt(
        ArtifactType.COVERAGEMATRIX,
        template,
        "BR-1: login",
        None,
        5,
        testcases="- TC-01 (positive, BR-1): вход",
    )

    assert "Test cases already generated for these requirements:" in prompt
    assert "- TC-01 (positive, BR-1): вход" in prompt


def test_requirement_rule_is_empty_without_br_ids() -> None:
    template = "Rule:\n{requirement_rule}\nRequirements:\n{content}"

    prompt = build_user_prompt(
        ArtifactType.TESTCASES, template, "no ids here", None, 5
    )

    assert "requirement id" not in prompt
    assert "no ids here" in prompt


def test_target_site_line_is_dropped_without_url() -> None:
    template = "Target site: {url}\n\nRequirements:\n{content}"

    prompt = build_user_prompt(
        ArtifactType.TESTPLAN, template, "body", None, 5
    )

    assert "Target site" not in prompt
    assert prompt.startswith("Requirements:")


def test_unknown_placeholder_raises() -> None:
    with pytest.raises(PromptError, match="nope"):
        build_user_prompt(
            ArtifactType.CHECKLIST, "Hello {nope}", "body", None, 5
        )


def test_missing_template_for_type_raises(tmp_path: Path) -> None:
    (tmp_path / "checklist.txt").write_text("only checklist", encoding="utf-8")

    with pytest.raises(PromptError, match="testcases.txt"):
        load_prompt_template(ArtifactType.TESTCASES, tmp_path)


def test_explicit_missing_prompt_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="does not exist"):
        resolve_prompt_dir(tmp_path / "missing")


def test_env_prompt_dir_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DZ2_PROMPT_DIR", str(tmp_path))

    assert resolve_prompt_dir(None) == tmp_path


def test_env_prompt_dir_missing_is_not_silently_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DZ2_PROMPT_DIR", str(tmp_path / "missing"))

    with pytest.raises(PromptError, match="DZ2_PROMPT_DIR"):
        resolve_prompt_dir(None)


def test_repository_ships_a_template_for_every_type() -> None:
    for artifact_type in ArtifactType:
        template = load_prompt_template(artifact_type)

        assert "{content}" in template
        assert not strip_template_comments(template).startswith("#")


def test_shipped_testcases_template_uses_singular_for_count_one() -> None:
    template = load_prompt_template(ArtifactType.TESTCASES)

    prompt = build_user_prompt(
        ArtifactType.TESTCASES, template, "BR-1: login", None, 1
    )

    assert "Generate exactly 1 test case: 1 positive and 0 negative." in prompt


DZ7_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "dz7" / "templates"


@pytest.mark.skipif(
    not DZ7_TEMPLATES_DIR.is_dir(), reason="dz7 sources are not checked out"
)
def test_dz2_templates_are_byte_identical_to_dz7() -> None:
    ours = Path(__file__).parent.parent / "prompts"

    for artifact_type in ArtifactType:
        name = f"{artifact_type.value}.txt"
        expected = (DZ7_TEMPLATES_DIR / name).read_bytes()
        actual = (ours / name).read_bytes()

        assert actual == expected, (
            f"{name} drifted from dz7/templates; run "
            "`python3 generator.py --emit-templates --output-dir ../dz2/prompts`"
        )
