from pathlib import Path

import pytest
from conftest import (
    FakeChat,
    balanced_set,
    capture_client_init,
    make_raw_cases,
    patch_client,
)

from ai_testgen.cli import EXIT_OK, main
from ai_testgen.config import Config, ConfigError, load_config


def write_config(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_when_no_file_anywhere(isolated_cwd: Path) -> None:
    assert load_config(None) == Config()


def test_full_config_loads(isolated_cwd: Path) -> None:
    path = write_config(
        isolated_cwd / "ai-testgen.toml",
        (
            '[ollama]\n'
            'base_url = "http://llama:11434"\n'
            'model = "llama3"\n'
            "timeout = 42.0\n"
            "[paths]\n"
            'input_dir = "docs/in"\n'
            'output_dir = "docs/out"\n'
            "[generation]\n"
            "temperature = 0.2\n"
            "max_retries = 5\n"
        ),
    )

    config = load_config(path)

    assert config.base_url == "http://llama:11434"
    assert config.model == "llama3"
    assert config.timeout == 42.0
    assert config.input_dir == Path("docs/in")
    assert config.output_dir == Path("docs/out")
    assert config.temperature == 0.2
    assert config.max_retries == 5


def test_partial_config_fills_defaults(isolated_cwd: Path) -> None:
    path = write_config(isolated_cwd / "ai-testgen.toml", '[ollama]\nmodel = "tiny"\n')

    config = load_config(path)

    assert config.model == "tiny"
    assert config.base_url == Config().base_url
    assert config.temperature == Config().temperature


def test_auto_discovery_in_current_directory(isolated_cwd: Path) -> None:
    write_config(isolated_cwd / "ai-testgen.toml", '[ollama]\nmodel = "auto"\n')

    assert load_config(None).model == "auto"


def test_explicit_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_broken_toml_raises(isolated_cwd: Path) -> None:
    path = write_config(isolated_cwd / "bad.toml", "[ollama\nbroken")

    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


def test_wrong_types_raise(isolated_cwd: Path) -> None:
    path = write_config(
        isolated_cwd / "ai-testgen.toml",
        "[generation]\ntemperature = \"hot\"\n",
    )

    with pytest.raises(ConfigError, match="temperature"):
        load_config(path)


def test_unknown_keys_ignored_with_warning(isolated_cwd: Path, capsys) -> None:
    path = write_config(
        isolated_cwd / "ai-testgen.toml",
        '[ollama]\nmodel = "m"\nfuture_option = 1\n',
    )

    config = load_config(path)

    assert config.model == "m"
    assert "future_option" in capsys.readouterr().out


def test_non_table_section_raises(isolated_cwd: Path) -> None:
    path = write_config(isolated_cwd / "ai-testgen.toml", "ollama = 7\n")

    with pytest.raises(ConfigError, match=r"\[ollama\]"):
        load_config(path)


def test_cli_model_from_config_without_env(monkeypatch, isolated_cwd: Path) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    write_config(
        isolated_cwd / "ai-testgen.toml",
        '[ollama]\nmodel = "config-model"\nbase_url = "http://cfg:11434"\n',
    )
    (isolated_cwd / "input").mkdir()
    (isolated_cwd / "input" / "doc.md").write_text("https://example.com", encoding="utf-8")
    captured = capture_client_init(monkeypatch)
    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10"])

    assert code == EXIT_OK
    assert captured["model"] == "config-model"
    assert captured["base_url"] == "http://cfg:11434"
