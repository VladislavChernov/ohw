"""Unit tests for the CLI orchestration (exit codes, error mapping)."""

import pytest

from llm_engine.cli import EXIT_GENERATION, EXIT_OK, EXIT_OLLAMA, EXIT_USAGE, main
from llm_engine.ollama_client import OllamaClient


@pytest.fixture
def configured(monkeypatch, tmp_path) -> object:
    monkeypatch.setenv("OLLAMA_MODEL", "qwen")
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.md").write_text("prompt content", encoding="utf-8")
    return input_dir


def test_no_model_returns_usage(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.md").write_text("hi", encoding="utf-8")
    rc = main(["--input-dir", str(input_dir), "--output-dir", str(tmp_path / "out")])
    assert rc == EXIT_USAGE


def test_missing_input_returns_usage(tmp_path) -> None:
    rc = main(["--input-dir", str(tmp_path / "nope"), "--output-dir", str(tmp_path / "out")])
    assert rc == EXIT_USAGE


def test_bad_temperature_returns_usage(tmp_path) -> None:
    rc = main(["--temperature", "9", "--input-dir", str(tmp_path), "--output-dir", str(tmp_path / "out")])
    assert rc == EXIT_USAGE


def test_ollama_unreachable_returns_3(configured) -> None:
    rc = main(
        [
            "--base-url", "http://127.0.0.1:1",
            "--input-dir", str(configured),
            "--output-dir", str(configured.parent / "out"),
            "--max-retries", "0",
        ]
    )
    assert rc == EXIT_OLLAMA


def test_success_writes_output(configured, monkeypatch) -> None:
    def fake_chat(self, user, system="", temperature=0.7):
        return "plain reply"

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    out = configured.parent / "out"
    rc = main(["--input-dir", str(configured), "--output-dir", str(out)])
    assert rc == EXIT_OK
    assert (out / "a.md").is_file()
    assert "plain reply" in (out / "a.md").read_text(encoding="utf-8")


def test_empty_reply_after_retries_returns_4(configured, monkeypatch) -> None:
    def empty_chat(self, user, system="", temperature=0.7):
        return "   "

    monkeypatch.setattr(OllamaClient, "chat", empty_chat)
    out = configured.parent / "out"
    rc = main(["--input-dir", str(configured), "--output-dir", str(out), "--max-retries", "1"])
    assert rc == EXIT_GENERATION
    assert not (out / "a.md").exists()