"""Tests for the vendored generation core (no network calls)."""

import json
from pathlib import Path

import pytest

from src.generator.handlers import get_handler
from src.generator.input_doc import InputError, load_docs
from src.generator.models import ArtifactType, CaseType
from src.generator.presets import select_extras
from src.generator.prompt import (
    build_user_prompt,
    load_prompt_template,
    resolve_prompt_dir,
)
from src.generator.renderer import render
from src.generator.validator import validate


def test_packaged_prompt_directory_is_resolved() -> None:
    directory = resolve_prompt_dir()
    assert directory.is_dir()
    assert (directory / "testcases.txt").is_file()
    assert (directory / "_system" / "system-json.txt").is_file()


def test_every_artifact_has_a_template() -> None:
    for artifact_type in ArtifactType:
        assert load_prompt_template(artifact_type).strip()


def test_user_prompt_removes_url_line_without_url() -> None:
    template = load_prompt_template(ArtifactType.TESTCASES)
    prompt = build_user_prompt(
        ArtifactType.TESTCASES,
        template,
        "BR-1. Система должна работать.",
        None,
        2,
    )
    assert "Target site:" not in prompt
    assert "BR-1" in prompt
    assert "{count}" not in prompt
    assert "{content}" not in prompt


def test_user_prompt_fills_counters() -> None:
    template = load_prompt_template(ArtifactType.TESTCASES)
    prompt = build_user_prompt(
        ArtifactType.TESTCASES,
        template,
        "BR-1. Требование.",
        "https://example.com",
        5,
    )
    assert "https://example.com" in prompt
    assert "5" in prompt


def test_validator_accepts_json_and_enforces_mix() -> None:
    raw = json.dumps(
        [
            {
                "id": "TC-01",
                "type": "positive",
                "title": "Успех",
                "steps": ["Шаг"],
                "expected": "Результат",
            },
            {
                "id": "TC-02",
                "type": "negative",
                "title": "Ошибка",
                "steps": ["Шаг"],
                "expected": "Ошибка",
            },
        ]
    )
    result = validate(raw, 2)
    assert result.ok
    assert len(result.cases) == 2


def test_validator_reports_invalid_json() -> None:
    result = validate("not json", 2)
    assert not result.ok
    assert "not valid JSON" in result.issues[0]


def test_validator_requires_a_negative_case() -> None:
    raw = json.dumps(
        [
            {
                "id": "TC-01",
                "type": "positive",
                "title": "Успех",
                "steps": ["Шаг"],
                "expected": "Результат",
            }
        ]
        * 2
    )
    result = validate(raw, 2)
    assert not result.ok
    assert any("negative" in issue for issue in result.issues)


def test_handler_uses_packaged_templates() -> None:
    handler = get_handler(ArtifactType.TESTCASES)
    assert handler.wants_json is True
    assert handler.build_system()


def test_render_contains_counts_and_sections() -> None:
    from src.generator.models import TestCase

    cases = [
        TestCase(
            id="TC-01",
            type=CaseType.POSITIVE,
            title="Успех",
            steps=["Шаг"],
            expected="Результат",
            requirement="BR-1",
        ),
        TestCase(
            id="TC-02",
            type=CaseType.NEGATIVE,
            title="Ошибка",
            steps=["Шаг"],
            expected="Ошибка",
        ),
    ]
    rendered = render(cases, "https://example.com", "requirements")
    assert "Позитивные сценарии" in rendered
    assert "Негативные сценарии" in rendered
    assert "https://example.com" in rendered
    assert "BR-1" in rendered


def test_load_docs_reads_markdown(tmp_path: Path) -> None:
    (tmp_path / "req.md").write_text(
        "BR-1. Система должна работать.\n", encoding="utf-8"
    )
    docs = load_docs(tmp_path, None)
    assert [doc.path.name for doc in docs] == ["req.md"]
    assert "BR-1" in docs[0].content


def test_load_docs_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        load_docs(tmp_path / "missing", None)


def test_load_docs_supports_front_matter_type(tmp_path: Path) -> None:
    (tmp_path / "req.md").write_text(
        "---\ntype: checklist\n---\nBR-1. Требование.\n", encoding="utf-8"
    )
    docs = load_docs(tmp_path, None)
    assert docs[0].artifact_type is ArtifactType.CHECKLIST


def test_preset_selection_rejects_unknown_id() -> None:
    from src.generator.presets import PresetError, load_presets, resolve_presets_dir

    presets_dir = resolve_presets_dir()
    assert presets_dir is not None
    presets = load_presets(presets_dir)
    with pytest.raises(PresetError):
        select_extras(presets, ["no-such-preset"], ArtifactType.TESTCASES)
