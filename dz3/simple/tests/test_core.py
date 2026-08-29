"""Tests for api_testgen."""

from pathlib import Path

import pytest

from api_testgen.extractor import extract_code
from api_testgen.prompt import load_prompt, resolve_prompt_path


def test_load_prompt_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompt(tmp_path / "prompt.txt")


def test_load_prompt_reads_file(tmp_path):
    file = tmp_path / "prompt.txt"
    file.write_text("Hello world", encoding="utf-8")
    assert load_prompt(file) == "Hello world"


def test_resolve_prompt_path_explicit():
    assert resolve_prompt_path("custom.txt", Path("input")) == Path("custom.txt")


def test_resolve_prompt_path_default():
    assert resolve_prompt_path(None, Path("input")) == Path("input") / "prompt.txt"


def test_extract_code_from_fences():
    response = "Here is the code:\n```python\nimport requests\ndef test_ok():\n    pass\n```\n"
    code = extract_code(response)
    assert "def test_ok" in code
    assert "```" not in code


def test_extract_code_bare_block():
    response = "import requests\n\ndef test_1():\n    pass\n"
    code = extract_code(response)
    assert "def test_1" in code
