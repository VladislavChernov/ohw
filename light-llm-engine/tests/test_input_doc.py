"""Unit tests for reading request files."""

import pytest

from llm_engine.input_doc import InputError, load_docs


def _make(tmp_path, names: list[str]) -> None:
    for name in names:
        (tmp_path / name).write_text(f"content-{name}", encoding="utf-8")


def test_loads_supported(tmp_path) -> None:
    _make(tmp_path, ["auth.md", "cart.txt", "notes.pdf"])
    docs = load_docs(tmp_path)
    names = [d.path.name for d in docs]
    assert names == ["auth.md", "cart.txt"]
    assert docs[0].content == "content-auth.md"


def test_empty_dir_raises(tmp_path) -> None:
    with pytest.raises(InputError):
        load_docs(tmp_path)


def test_missing_dir_raises(tmp_path) -> None:
    with pytest.raises(InputError):
        load_docs(tmp_path / "nope")


def test_no_supported_files_raises(tmp_path) -> None:
    _make(tmp_path, ["a.pdf", "b.bin"])
    with pytest.raises(InputError):
        load_docs(tmp_path)


def test_name_collision_raises(tmp_path) -> None:
    _make(tmp_path, ["auth.md", "auth.txt"])
    with pytest.raises(InputError) as exc:
        load_docs(tmp_path)
    assert "collision" in str(exc.value)