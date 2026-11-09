"""Configuration loading, precedence and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_degradation.config import (
    ConfigError,
    LabConfig,
    load_config,
    resolve_config_path,
    validate_config,
)


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_builtin_defaults() -> None:
    config = LabConfig()
    assert config.base_url == "http://ohw-ollama:11434"
    assert config.model == "llama3"
    assert config.max_retries == 3
    assert config.thresholds.hallucination_rate_max == 0.25


def test_reads_toml_values(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
[ollama]
base_url = "http://example.test:11434"
model = "tiny"
timeout = 12.5
temperature = 0.2
seed = 7
num_ctx = 2048
num_predict = 64
max_retries = 1

[thresholds]
hallucination_rate_max = 0.5
""",
    )
    config = load_config(path, env={})
    assert config.model == "tiny"
    assert config.timeout == 12.5
    assert config.seed == 7
    assert config.thresholds.hallucination_rate_max == 0.5
    assert config.config_path == path


def test_environment_overrides_toml(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[ollama]\nmodel = "from-file"\n')
    config = load_config(
        path,
        env={"LLM_LAB_MODEL": "from-env", "OLLAMA_BASE_URL": "http://env:11434"},
    )
    assert config.model == "from-env"
    assert config.base_url == "http://env:11434"


def test_cli_overrides_environment(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[ollama]\nmodel = "from-file"\n')
    config = load_config(
        path,
        overrides={"model": "from-cli", "temperature": 1.1},
        env={"LLM_LAB_MODEL": "from-env"},
    )
    assert config.model == "from-cli"
    assert config.temperature == 1.1


def test_unknown_section_and_key_become_warnings(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        '[mystery]\nx = 1\n[ollama]\nmodel = "llama3"\nunknown = true\n',
    )
    config = load_config(path, env={})
    assert any("unknown section [mystery]" in item for item in config.warnings)
    assert any("unknown key [ollama].unknown" in item for item in config.warnings)


def test_unknown_threshold_is_an_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[thresholds]\nnot_a_threshold = 0.1\n")
    with pytest.raises(ConfigError, match="unknown threshold"):
        load_config(path, env={})


def test_wrong_type_is_an_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[ollama]\ntemperature = "hot"\n')
    with pytest.raises(ConfigError, match="temperature"):
        load_config(path, env={})


def test_invalid_base_url_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[ollama]\nbase_url = "ftp://example.test"\n')
    with pytest.raises(ConfigError, match="base_url"):
        load_config(path, env={})


def test_trailing_slash_on_base_url_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[ollama]\nbase_url = "http://example.test:11434/"\n')
    with pytest.raises(ConfigError, match="must not end"):
        load_config(path, env={})


def test_seed_can_be_disabled_through_environment(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[ollama]\nseed = 42\n")
    config = load_config(path, env={"LLM_LAB_SEED": "none"})
    assert config.seed is None


def test_paths_are_resolved_against_config_directory(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[paths]\ninput_dir = "docs"\noutput_dir = "out"\n')
    config = load_config(path, env={})
    assert config.input_dir == (tmp_path / "docs").resolve()
    assert config.output_dir == (tmp_path / "out").resolve()


def test_probe_prompt_dir_is_resolved_and_overridable(tmp_path: Path) -> None:
    path = write_config(tmp_path, '[paths]\nprobe_prompt_dir = "prompts"\n')
    config = load_config(path, env={})
    assert config.probe_prompt_dir == (tmp_path / "prompts").resolve()

    from_env = load_config(path, env={"DZ8_PROMPT_DIR": str(tmp_path / "other")})
    assert from_env.probe_prompt_dir == tmp_path / "other"

    from_cli = load_config(
        path, overrides={"probe_prompt_dir": tmp_path / "cli"}, env={}
    )
    assert from_cli.probe_prompt_dir == tmp_path / "cli"


def test_missing_explicit_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        resolve_config_path(tmp_path / "missing.toml", env={})


def test_validate_config_range_checks() -> None:
    with pytest.raises(ConfigError, match="timeout"):
        validate_config(LabConfig(timeout=0))
    with pytest.raises(ConfigError, match="num_ctx"):
        validate_config(LabConfig(num_ctx=16))
    with pytest.raises(ConfigError, match="max_retries"):
        validate_config(LabConfig(max_retries=-1))


def test_keep_runs_defaults_and_overrides(tmp_path: Path) -> None:
    assert LabConfig().keep_runs == 3
    path = write_config(tmp_path, "[reports]\nkeep_runs = 2\n")
    assert load_config(path, env={}).keep_runs == 2
    assert load_config(path, env={"LLM_LAB_KEEP_RUNS": "5"}).keep_runs == 5
    overridden = load_config(path, overrides={"keep_runs": 1}, env={"LLM_LAB_KEEP_RUNS": "5"})
    assert overridden.keep_runs == 1


def test_keep_runs_must_be_positive() -> None:
    with pytest.raises(ConfigError, match="keep_runs"):
        validate_config(LabConfig(keep_runs=0))
