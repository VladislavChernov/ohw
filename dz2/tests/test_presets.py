from pathlib import Path

import pytest

from ai_testgen.models import ArtifactType
from ai_testgen.presets import (
    Preset,
    PresetError,
    load_presets,
    resolve_presets_dir,
    select_extras,
)
from ai_testgen.prompt import PromptError, build_user_prompt


def test_repository_ships_presets() -> None:
    presets = load_presets(Path(__file__).parent.parent / "presets")

    assert len(presets) >= 5
    for preset_id, preset in presets.items():
        assert preset.id == preset_id
        assert preset.label
        assert preset.text
        assert preset.types


def test_every_shipped_preset_targets_known_types() -> None:
    presets = load_presets(Path(__file__).parent.parent / "presets")

    for preset in presets.values():
        for artifact_type in preset.types:
            assert isinstance(artifact_type, ArtifactType)


def test_extras_are_injected_into_extra_placeholder() -> None:
    prompt = build_user_prompt(
        ArtifactType.TESTCASES,
        "Task\n{extra}\nRequirements:\n{content}",
        "BR-1: login",
        None,
        5,
        extras=["Проверь пустой список.", "Добавь границы."],
    )

    assert "Additional instructions:" in prompt
    assert "do not add pages" in prompt
    assert "- Проверь пустой список." in prompt
    assert "- Добавь границы." in prompt


def test_no_extras_leaves_no_instruction_block() -> None:
    prompt = build_user_prompt(
        ArtifactType.TESTCASES,
        "Task\n{extra}\nRequirements:\n{content}",
        "BR-1: login",
        None,
        5,
    )

    assert "Additional instructions:" not in prompt


def test_presets_require_extra_placeholder() -> None:
    with pytest.raises(PromptError, match=r"\{extra\}"):
        build_user_prompt(
            ArtifactType.CHECKLIST,
            "Task\n{content}",
            "body",
            None,
            5,
            extras=["extra text"],
        )


def test_select_extras_returns_preset_texts() -> None:
    presets = {
        "one": Preset(
            id="one",
            label="One",
            text="do one",
            types=(ArtifactType.TESTCASES, ArtifactType.CHECKLIST),
        )
    }

    assert select_extras(presets, ["one"], ArtifactType.TESTCASES) == ["do one"]
    assert select_extras(presets, [], ArtifactType.TESTCASES) == []


def test_select_extras_rejects_unknown_id() -> None:
    with pytest.raises(PresetError, match="unknown preset"):
        select_extras({}, ["missing"], ArtifactType.TESTCASES)


def test_select_extras_rejects_wrong_type() -> None:
    presets = load_presets(Path(__file__).parent.parent / "presets")

    with pytest.raises(PresetError, match="does not apply"):
        select_extras(presets, ["localization"], ArtifactType.COVERAGEMATRIX)


def test_preset_id_must_match_file_name(tmp_path: Path) -> None:
    (tmp_path / "name.toml").write_text(
        'id = "other"\nlabel = "L"\ntext = "T"\n', encoding="utf-8"
    )

    with pytest.raises(PresetError, match="must match"):
        load_presets(tmp_path)


def test_preset_requires_label_and_text(tmp_path: Path) -> None:
    (tmp_path / "broken.toml").write_text('id = "broken"\n', encoding="utf-8")

    with pytest.raises(PresetError, match="label"):
        load_presets(tmp_path)


def test_preset_with_unknown_type_raises(tmp_path: Path) -> None:
    (tmp_path / "bad.toml").write_text(
        'id = "bad"\nlabel = "L"\ntext = "T"\ntypes = ["slides"]\n',
        encoding="utf-8",
    )

    with pytest.raises(PresetError, match="unknown artifact type"):
        load_presets(tmp_path)


def test_preset_types_default_to_every_type(tmp_path: Path) -> None:
    (tmp_path / "all.toml").write_text(
        'id = "all"\nlabel = "L"\ntext = "T"\n', encoding="utf-8"
    )

    preset = load_presets(tmp_path)["all"]

    assert set(preset.types) == set(ArtifactType)


def test_env_presets_dir_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DZ2_PRESETS_DIR", str(tmp_path))

    assert resolve_presets_dir(None) == tmp_path


def test_missing_env_presets_dir_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DZ2_PRESETS_DIR", str(tmp_path / "missing"))

    with pytest.raises(PresetError, match="DZ2_PRESETS_DIR"):
        resolve_presets_dir(None)


def test_explicit_missing_presets_dir_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PresetError, match="does not exist"):
        resolve_presets_dir(tmp_path / "missing")


DZ7_PRESETS_DIR = Path(__file__).parent.parent.parent / "dz7" / "presets"


@pytest.mark.skipif(
    not DZ7_PRESETS_DIR.is_dir(), reason="dz7 sources are not checked out"
)
def test_dz2_presets_are_byte_identical_to_dz7() -> None:
    ours = Path(__file__).parent.parent / "presets"
    expected_names = {path.name for path in DZ7_PRESETS_DIR.glob("*.toml")}
    actual_names = {path.name for path in ours.glob("*.toml")}

    assert actual_names == expected_names, (
        "preset set drifted from dz7/presets; run "
        "`python3 generator.py --emit-presets --output-dir ../dz2/presets`"
    )
    for name in sorted(expected_names):
        assert (ours / name).read_bytes() == (DZ7_PRESETS_DIR / name).read_bytes(), (
            f"{name} drifted from dz7/presets"
        )
