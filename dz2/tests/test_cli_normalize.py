from pathlib import Path

import pytest
from conftest import (
    FakeChat,
    balanced_set,
    make_raw_cases,
    patch_client,
)

from ai_testgen.cli import EXIT_OK, EXIT_USAGE, main

CP1251_DOC = """---
type: testcases
---
# Требования: вход в личный кабинет

Сайт: https://shop.example.com

## BR-1 Вход по email

Форма принимает email и пароль.

## BR-2 Неверный пароль

Показывается ошибка.
"""


@pytest.fixture
def workbench(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


NO_ID_DOC = """# Требования к корзине

https://shop.example.com/cart

## Требования

- вход в корзину
- выход из корзины
"""


def _write_cp1251(directory: Path, name: str = "doc.md") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(CP1251_DOC.encode("cp1251"))
    return path


def test_normalize_only_needs_no_count_and_no_model(
    workbench: Path,
    input_dir: Path,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_cp1251(input_dir)

    code = main(
        [
            "--normalize-only",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_OK
    path = output_dir / "normalized" / "doc.md"
    text = path.read_text(encoding="utf-8")
    assert "## Requirements" in text
    assert "### BR-1" in text
    assert "### BR-2" in text
    assert "Форма принимает email и пароль." in text
    printed = capsys.readouterr()
    assert "cp1251" in printed.out
    assert "2 requirements" in printed.out


def test_normalize_only_uses_requested_directory(
    workbench: Path, input_dir: Path, tmp_path: Path
) -> None:
    _write_cp1251(input_dir)
    target = tmp_path / "custom"

    code = main(
        [
            "--normalize-only",
            "--input-dir",
            str(input_dir),
            "--normalized-dir",
            str(target),
        ]
    )

    assert code == EXIT_OK
    assert (target / "doc.md").is_file()


def test_normalize_only_does_not_call_the_model(
    workbench: Path,
    input_dir: Path,
    output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_cp1251(input_dir)
    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--normalize-only",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_OK
    assert fake.calls == []


def test_normalize_only_missing_input_directory_returns_2(
    workbench: Path, tmp_path: Path
) -> None:
    code = main(
        [
            "--normalize-only",
            "--input-dir",
            str(tmp_path / "nowhere"),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert code == EXIT_USAGE


def test_count_is_still_required_for_generation(
    workbench: Path, input_dir: Path, output_dir: Path
) -> None:
    _write_cp1251(input_dir)

    with pytest.raises(SystemExit) as exc_info:
        main(["--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert exc_info.value.code == EXIT_USAGE


def test_normalize_feeds_canonical_text_to_the_prompt(
    workbench: Path,
    input_dir: Path,
    output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    _write_cp1251(input_dir)
    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "-n",
            "10",
            "--normalize",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_OK
    prompt = fake.calls[0]
    assert "## Requirements" in prompt
    assert "### BR-1" in prompt
    assert "### BR-2" in prompt
    # --normalize transforms in memory: only the artifact lands in output/.
    assert sorted(item.name for item in output_dir.iterdir()) == ["doc.md"]


def test_normalize_only_fails_on_empty_document(
    workbench: Path, input_dir: Path, output_dir: Path
) -> None:
    input_dir.mkdir()
    (input_dir / "empty.md").write_text("# Только заголовок\n", encoding="utf-8")

    code = main(
        [
            "--normalize-only",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_USAGE


def test_normalizer_config_file_changes_the_canonical_form(
    workbench: Path, input_dir: Path, output_dir: Path
) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "doc.md").write_text(NO_ID_DOC, encoding="utf-8")
    config = workbench / "custom.toml"
    config.write_text(
        'id_prefix = "REQ"\nrequirements_heading = "Требования системы"\n',
        encoding="utf-8",
    )

    code = main(
        [
            "--normalize-only",
            "--normalizer-config",
            str(config),
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_OK
    text = (output_dir / "normalized" / "doc.md").read_text(encoding="utf-8")
    assert "## Требования системы" in text
    assert "### REQ-1" in text
    assert "### REQ-2" in text


def test_normalizer_toml_is_picked_up_from_the_working_directory(
    workbench: Path, input_dir: Path, output_dir: Path
) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "doc.md").write_text(NO_ID_DOC, encoding="utf-8")
    (workbench / "normalizer.toml").write_text('id_prefix = "REQ"\n', encoding="utf-8")

    code = main(
        [
            "--normalize-only",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_OK
    text = (output_dir / "normalized" / "doc.md").read_text(encoding="utf-8")
    assert "### REQ-1" in text


def test_missing_normalizer_config_returns_2(
    workbench: Path, input_dir: Path, output_dir: Path
) -> None:
    _write_cp1251(input_dir)

    code = main(
        [
            "--normalize-only",
            "--normalizer-config",
            str(workbench / "absent.toml"),
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_USAGE


def test_invalid_normalizer_config_returns_2(
    workbench: Path, input_dir: Path, output_dir: Path
) -> None:
    _write_cp1251(input_dir)
    config = workbench / "broken.toml"
    config.write_text('id_prefix = "BR-1"\n', encoding="utf-8")

    code = main(
        [
            "--normalize-only",
            "--normalizer-config",
            str(config),
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == EXIT_USAGE
