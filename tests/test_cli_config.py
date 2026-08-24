from pathlib import Path

import pytest
from conftest import (
    FakeChat,
    balanced_set,
    capture_client_init,
    make_raw_cases,
    patch_client,
    write_input,
)

from ai_testgen.cli import EXIT_OK, EXIT_USAGE, main


@pytest.fixture
def config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli_temperature_overrides_config(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    config_env: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    write_input(input_dir, "doc.md", "https://example.com")
    (config_env / "ai-testgen.toml").write_text(
        "[generation]\ntemperature = 0.9\n",
        encoding="utf-8",
    )
    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--temperature",
            "0.2",
        ]
    )

    assert code == EXIT_OK
    assert fake.temps == [0.2]


def test_config_temperature_used_without_cli(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    config_env: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    write_input(input_dir, "doc.md", "https://example.com")
    (config_env / "ai-testgen.toml").write_text(
        "[generation]\ntemperature = 0.9\n",
        encoding="utf-8",
    )
    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert fake.temps == [0.9]


def test_env_overrides_config_model_and_url(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    config_env: Path,
) -> None:
    (config_env / "ai-testgen.toml").write_text(
        '[ollama]\nmodel = "config-model"\nbase_url = "http://cfg:11434"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env:11434")
    captured = capture_client_init(monkeypatch)
    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)
    write_input(input_dir, "doc.md", "https://example.com")

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert captured["model"] == "env-model"
    assert captured["base_url"] == "http://env:11434"


def test_max_retries_from_config_limits_attempts(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    config_env: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    write_input(input_dir, "doc.md", "https://example.com")
    (config_env / "ai-testgen.toml").write_text(
        "[generation]\nmax_retries = 1\n",
        encoding="utf-8",
    )
    fake = FakeChat([make_raw_cases(balanced_set(10)[:3])] * 5)
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == 4
    assert len(fake.calls) == 2


def test_invalid_temperature_in_config_returns_2(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    config_env: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    write_input(input_dir, "doc.md", "https://example.com")
    (config_env / "ai-testgen.toml").write_text(
        "[generation]\ntemperature = 9.9\n",
        encoding="utf-8",
    )
    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_USAGE
    assert fake.calls == []


def test_explicit_config_flag_is_respected(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    config_env: Path,
) -> None:
    custom = config_env / "custom.toml"
    custom.write_text('[ollama]\nmodel = "custom-model"\n', encoding="utf-8")
    captured = capture_client_init(monkeypatch)
    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)
    write_input(input_dir, "doc.md", "https://example.com")

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--config",
            str(custom),
        ]
    )

    assert code == EXIT_OK
    assert captured["model"] == "custom-model"
