"""Tests for ohw_kit.io (extensible input reading)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ohw_kit.io import InputError, load_input, register_reader


def _make(tmp_path: Path, names: list[str]) -> None:
    for name in names:
        (tmp_path / name).write_text(f"content-{name}", encoding="utf-8")


def test_loads_supported(tmp_path: Path) -> None:
    _make(tmp_path, ["auth.md", "cart.txt", "notes.pdf"])
    docs = load_input(tmp_path)
    assert [(d.path.name, d.extension) for d in docs] == [
        ("auth.md", ".md"),
        ("cart.txt", ".txt"),
    ]
    assert docs[0].content == "content-auth.md"


def test_content_preserved(tmp_path: Path) -> None:
    _make(tmp_path, ["a.txt"])
    assert load_input(tmp_path)[0].content == "content-a.txt"


def test_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        load_input(tmp_path)


def test_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        load_input(tmp_path / "nope")


def test_no_supported_files_raises(tmp_path: Path) -> None:
    _make(tmp_path, ["a.bin"])
    with pytest.raises(InputError):
        load_input(tmp_path)


def test_name_collision_raises(tmp_path: Path) -> None:
    _make(tmp_path, ["auth.md", "auth.txt"])
    with pytest.raises(InputError) as exc:
        load_input(tmp_path)
    assert "auth" in str(exc.value)


def test_recursive_option(tmp_path: Path) -> None:
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "deep.txt").write_text("deep", encoding="utf-8")
    docs = load_input(tmp_path, recursively=True)
    assert [d.path.name for d in docs] == ["deep.txt"]


def test_unknown_extension_config_raises(tmp_path: Path) -> None:
    _make(tmp_path, ["a.pdf"])
    with pytest.raises(InputError) as exc:
        load_input(tmp_path, extensions=(".pdf",))
    assert "no reader registered" in str(exc.value)


def test_register_custom_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []

    @register_reader(".pdf")
    def _fake_pdf(path: Path) -> str:
        calls.append(path)
        return "PDF-CONTENT"

    _make(tmp_path, ["a.pdf"])
    docs = load_input(tmp_path, extensions=(".pdf",))
    assert docs[0].content == "PDF-CONTENT"
    assert calls == [tmp_path / "a.pdf"]

    # clean up registry so other tests are unaffected
    from ohw_kit import io

    monkeypatch.delitem(io._READERS, ".pdf")


def test_register_bad_extension_raises() -> None:
    with pytest.raises(ValueError):

        @register_reader("pdf")
        def _bad(path: Path) -> str:
            return ""