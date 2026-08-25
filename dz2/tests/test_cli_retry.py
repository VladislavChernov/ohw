import json
from pathlib import Path

import pytest
from conftest import (
    FakeChat,
    balanced_set,
    make_case,
    make_raw_cases,
    patch_client,
    write_input,
)

from ai_testgen.cli import EXIT_GENERATION, EXIT_OK, EXIT_OLLAMA, EXIT_USAGE, main
from ai_testgen.ollama_client import OllamaConnectionError


@pytest.fixture
def env_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.chdir(tmp_path)


def test_retry_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    env_setup: None,
) -> None:
    write_input(input_dir, "auth.md", "test auth on https://example.com")
    good = make_raw_cases(balanced_set(10))
    fake = FakeChat([make_raw_cases(balanced_set(10)[:8]), good])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert len(fake.calls) == 2
    assert "violated the constraints" in fake.calls[1]
    written = (output_dir / "auth.md").read_text(encoding="utf-8")
    assert written.count("### TC-") == 10


def test_retries_exhausted_returns_4_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    env_setup: None,
) -> None:
    write_input(input_dir, "auth.md", "test auth on https://example.com")
    fake = FakeChat([make_raw_cases(balanced_set(10)[:5])] * 4)
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--max-retries",
            "3",
        ]
    )

    assert code == EXIT_GENERATION
    assert len(fake.calls) == 4
    assert not output_dir.exists() or not any(output_dir.iterdir())


def test_batch_two_files_two_outputs(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    env_setup: None,
) -> None:
    write_input(input_dir, "auth.md", "auth on https://a.com")
    write_input(input_dir, "cart.txt", "cart on https://b.com")
    fake = FakeChat([make_raw_cases(balanced_set(10))] * 2)
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert (output_dir / "auth.md").exists()
    assert (output_dir / "cart.md").exists()


def test_name_collision_returns_2_without_llm_call(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    env_setup: None,
) -> None:
    write_input(input_dir, "auth.md", "one")
    write_input(input_dir, "auth.txt", "two")
    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_USAGE
    assert fake.calls == []


def test_unreachable_ollama_returns_3(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    env_setup: None,
) -> None:
    write_input(input_dir, "auth.md", "https://example.com")
    fake = FakeChat([OllamaConnectionError("boom")])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OLLAMA


def test_missing_model_env_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    isolated_cwd: Path,
) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    write_input(input_dir, "auth.md", "content")

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == 2


def test_invalid_count_rejected_without_llm_call(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    env_setup: None,
) -> None:
    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    with pytest.raises(SystemExit) as exc_info:
        main(["--count", "0", "--input-dir", str(input_dir)])

    assert exc_info.value.code == 2
    assert fake.calls == []


def test_url_override_applied_to_prompt(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    env_setup: None,
) -> None:
    write_input(input_dir, "doc.md", "no url inside")
    captured = {}

    def spy(system: str, prompt: str, temperature: float, *, json_mode: bool = True) -> str:
        captured["prompt"] = prompt
        return make_raw_cases(balanced_set(10))

    patch_client(monkeypatch, spy)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--url",
            "https://override.com",
        ]
    )

    assert code == EXIT_OK
    assert "https://override.com" in captured["prompt"]


def test_single_case_response_shape() -> None:
    raw = make_raw_cases([make_case()])
    data = json.loads(raw)
    assert data[0]["type"] == "positive"
