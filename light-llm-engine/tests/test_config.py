"""Unit tests for configuration loading."""

from pathlib import Path

import pytest

from llm_engine.config import (
    DEFAULT_TIMEOUT,
    ConfigError,
    load_config,
)
from llm_engine.ollama_client import DEFAULT_BASE_URL


def test_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config(None)
    assert config.base_url == DEFAULT_BASE_URL
    assert config.model == ""
    assert config.timeout == DEFAULT_TIMEOUT
    assert config.input_dir.name == "input"
    assert config.output_dir.name == "output"
    assert config.temperature == 0.7
    assert config.max_retries == 3


def test_missing_file_is_not_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config(None)
    assert config.model == ""


def test_broken_toml_raises(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "light-llm-engine.toml"
    cfg.write_text("this is [not tomll", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        load_config(None)


def test_explicit_missing_file_raises(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_reads_values(tmp_path, monkeypatch) -> None:
    (tmp_path / "light-llm-engine.toml").write_text(
        "[ollama]\nbase_url = \"http://x:11434\"\nmodel = \"qwen\"\n"
        "[paths]\ninput_dir = \"in\"\noutput_dir = \"out\"\n"
        "[generation]\ntemperature = 0.2\nmax_retries = 5\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(None)
    assert config.base_url == "http://x:11434"
    assert config.model == "qwen"
    assert config.input_dir == Path("in")
    assert config.output_dir == Path("out")
    assert config.temperature == 0.2
    assert config.max_retries == 5


def test_wrong_type_raises(tmp_path, monkeypatch) -> None:
    (tmp_path / "light-llm-engine.toml").write_text(
        "[generation]\ntemperature = \"hot\"\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        load_config(None)