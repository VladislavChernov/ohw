"""Configuration loading and validation.

Precedence: CLI overrides > environment > ``config.toml`` > defaults.
Paths in TOML are resolved against the config file directory; relative
defaults are resolved against the repository root (``dz8``).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE_NAME = "config.toml"

DEFAULT_BASE_URL = "http://ohw-ollama:11434"
DEFAULT_MODEL = "llama3"
DEFAULT_TIMEOUT = 600.0
DEFAULT_TEMPERATURE = 0.7
DEFAULT_SEED: int | None = 42
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 512
DEFAULT_MAX_RETRIES = 3
DEFAULT_KEEP_RUNS = 3

_KNOWN_SECTIONS = {"ollama", "paths", "thresholds", "reports"}
_OLLAMA_KEYS = {
    "base_url",
    "model",
    "timeout",
    "temperature",
    "seed",
    "num_ctx",
    "num_predict",
    "max_retries",
}
_PATHS_KEYS = {
    "input_dir",
    "output_dir",
    "golden_dir",
    "prompt_dir",
    "probe_prompt_dir",
    "presets_dir",
    "normalizer_config",
}
_REPORTS_KEYS = {"keep_runs"}


class ConfigError(Exception):
    """Configuration is missing, malformed or out of range (exit code 2)."""


@dataclass(frozen=True)
class Thresholds:
    hallucination_rate_max: float = 0.25
    validator_pass_rate_min: float = 0.90
    count_match_rate_min: float = 1.0
    br_coverage_min: float = 0.80
    tc_jaccard_mean_min: float = 0.40
    heading_jaccard_mean_min: float = 0.70
    canary_title_overlap_min: float = 0.60
    canary_body_overlap_mean_min: float = 0.85
    canary_body_overlap_min: float = 0.50
    degradation_pass_drop_max: float = 0.20
    degradation_br_drop_max: float = 0.10
    degradation_json_drop_max: float = 0.20
    degradation_attempts_increase_max: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return {
            "hallucination_rate_max": self.hallucination_rate_max,
            "validator_pass_rate_min": self.validator_pass_rate_min,
            "count_match_rate_min": self.count_match_rate_min,
            "br_coverage_min": self.br_coverage_min,
            "tc_jaccard_mean_min": self.tc_jaccard_mean_min,
            "heading_jaccard_mean_min": self.heading_jaccard_mean_min,
            "canary_title_overlap_min": self.canary_title_overlap_min,
            "canary_body_overlap_mean_min": self.canary_body_overlap_mean_min,
            "canary_body_overlap_min": self.canary_body_overlap_min,
            "degradation_pass_drop_max": self.degradation_pass_drop_max,
            "degradation_br_drop_max": self.degradation_br_drop_max,
            "degradation_json_drop_max": self.degradation_json_drop_max,
            "degradation_attempts_increase_max": self.degradation_attempts_increase_max,
        }


@dataclass(frozen=True)
class LabConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    temperature: float = DEFAULT_TEMPERATURE
    seed: int | None = DEFAULT_SEED
    num_ctx: int = DEFAULT_NUM_CTX
    num_predict: int = DEFAULT_NUM_PREDICT
    max_retries: int = DEFAULT_MAX_RETRIES

    root: Path = field(default_factory=lambda: Path.cwd())
    input_dir: Path = field(default_factory=lambda: Path("input"))
    output_dir: Path = field(default_factory=lambda: Path("output"))
    golden_dir: Path = field(default_factory=lambda: Path("golden"))
    prompt_dir: Path | None = None
    probe_prompt_dir: Path | None = None
    presets_dir: Path | None = None
    normalizer_config: Path | None = None
    keep_runs: int = DEFAULT_KEEP_RUNS

    thresholds: Thresholds = field(default_factory=Thresholds)
    config_path: Path | None = None
    warnings: tuple[str, ...] = ()


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_root(env: Mapping[str, str]) -> Path:
    raw = env.get("LLM_LAB_ROOT")
    if raw:
        return Path(raw).expanduser()
    return _package_root()


def resolve_config_path(
    path: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    environment = os.environ if env is None else env
    if path is not None:
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            raise ConfigError(f"config file not found: {candidate}")
        return candidate

    env_path = environment.get("LLM_LAB_CONFIG")
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_file():
            raise ConfigError(f"config file not found: {candidate}")
        return candidate

    candidates = []
    root = environment.get("LLM_LAB_ROOT")
    if root:
        candidates.append(Path(root).expanduser() / CONFIG_FILE_NAME)
    candidates.append(Path.cwd() / CONFIG_FILE_NAME)
    candidates.append(_package_root() / CONFIG_FILE_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(
    path: str | Path | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> LabConfig:
    """Load, merge and validate configuration."""
    environment = os.environ if env is None else env
    overrides = dict(overrides or {})
    warnings: list[str] = []

    config_path = resolve_config_path(path, env=environment)
    root = _default_root(environment)
    raw: dict[str, Any] = {}
    base_dir = root

    if config_path is not None:
        base_dir = config_path.parent
        try:
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"cannot read config {config_path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    else:
        warnings.append("config.toml not found; using built-in defaults")

    warnings.extend(_check_unknown_keys(raw, config_path))

    ollama = _table(raw, "ollama")
    paths = _table(raw, "paths")
    thresholds_raw = _table(raw, "thresholds")
    reports = _table(raw, "reports")

    base_url = _string(ollama, "base_url", DEFAULT_BASE_URL, "ollama.base_url")
    model = _string(ollama, "model", DEFAULT_MODEL, "ollama.model")
    timeout = _number(ollama, "timeout", DEFAULT_TIMEOUT, "ollama.timeout")
    temperature = _number(ollama, "temperature", DEFAULT_TEMPERATURE, "ollama.temperature")
    seed = _optional_int(ollama, "seed", DEFAULT_SEED, "ollama.seed")
    num_ctx = _integer(ollama, "num_ctx", DEFAULT_NUM_CTX, "ollama.num_ctx")
    num_predict = _integer(ollama, "num_predict", DEFAULT_NUM_PREDICT, "ollama.num_predict")
    max_retries = _integer(ollama, "max_retries", DEFAULT_MAX_RETRIES, "ollama.max_retries")

    input_dir = _resolve_path(paths, "input_dir", "input", base_dir, root)
    output_dir = _resolve_path(paths, "output_dir", "output", base_dir, root)
    golden_dir = _resolve_path(paths, "golden_dir", "golden", base_dir, root)
    prompt_dir = _optional_path(paths, "prompt_dir", base_dir)
    probe_prompt_dir = _optional_path(paths, "probe_prompt_dir", base_dir)
    presets_dir = _optional_path(paths, "presets_dir", base_dir)
    normalizer_config = _optional_path(paths, "normalizer_config", base_dir)
    keep_runs = _integer(reports, "keep_runs", DEFAULT_KEEP_RUNS, "reports.keep_runs")

    # Environment overrides.
    base_url = environment.get("LLM_LAB_BASE_URL") or environment.get("OLLAMA_BASE_URL") or base_url
    model = environment.get("LLM_LAB_MODEL") or environment.get("OLLAMA_MODEL") or model
    timeout = _env_number(environment, ("LLM_LAB_TIMEOUT", "OLLAMA_TIMEOUT"), timeout)
    temperature = _env_number(environment, ("LLM_LAB_TEMPERATURE",), temperature)
    seed = _env_optional_int(environment, ("LLM_LAB_SEED",), seed)
    num_ctx = _env_integer(environment, ("LLM_LAB_NUM_CTX",), num_ctx)
    num_predict = _env_integer(environment, ("LLM_LAB_NUM_PREDICT",), num_predict)
    max_retries = _env_integer(environment, ("LLM_LAB_MAX_RETRIES",), max_retries)
    keep_runs = _env_integer(environment, ("LLM_LAB_KEEP_RUNS",), keep_runs)

    input_dir = _env_path(environment, ("LLM_LAB_INPUT_DIR",), input_dir)
    output_dir = _env_path(environment, ("LLM_LAB_OUTPUT_DIR",), output_dir)
    golden_dir = _env_path(environment, ("LLM_LAB_GOLDEN_DIR",), golden_dir)
    prompt_dir = _env_optional_path(environment, "DZ2_PROMPT_DIR", prompt_dir)
    probe_prompt_dir = _env_optional_path(environment, "DZ8_PROMPT_DIR", probe_prompt_dir)
    presets_dir = _env_optional_path(environment, "DZ2_PRESETS_DIR", presets_dir)
    normalizer_config = _env_optional_path(
        environment, "AI_TESTGEN_NORMALIZER_CONFIG", normalizer_config
    )

    thresholds = _load_thresholds(thresholds_raw)

    # CLI overrides have the highest priority.
    for key in (
        "base_url",
        "model",
        "timeout",
        "temperature",
        "seed",
        "num_ctx",
        "num_predict",
        "max_retries",
        "keep_runs",
        "input_dir",
        "output_dir",
        "golden_dir",
        "prompt_dir",
        "probe_prompt_dir",
        "presets_dir",
        "normalizer_config",
        "root",
    ):
        if key not in overrides or overrides[key] is None:
            continue
        value = overrides[key]
        if key in {"input_dir", "output_dir", "golden_dir", "root"}:
            value = Path(value).expanduser()
        elif key in {"prompt_dir", "probe_prompt_dir", "presets_dir", "normalizer_config"}:
            value = Path(value).expanduser()
        elif key in {"timeout", "temperature"}:
            value = float(value)
        elif key in {"seed"}:
            value = None if value is None else int(value)
        elif key in {"num_ctx", "num_predict", "max_retries", "keep_runs"}:
            value = int(value)
        overrides[key] = value

    # Scalar CLI overrides were normalized above but the locals still hold the
    # environment/file values; promote them before building the config.
    base_url = overrides.get("base_url", base_url)
    model = overrides.get("model", model)
    timeout = overrides.get("timeout", timeout)
    temperature = overrides.get("temperature", temperature)
    seed = overrides.get("seed", seed)
    num_ctx = overrides.get("num_ctx", num_ctx)
    num_predict = overrides.get("num_predict", num_predict)
    max_retries = overrides.get("max_retries", max_retries)
    keep_runs = overrides.get("keep_runs", keep_runs)

    config = LabConfig(
        base_url=str(base_url),
        model=str(model),
        timeout=float(timeout),
        temperature=float(temperature),
        seed=seed,
        num_ctx=int(num_ctx),
        num_predict=int(num_predict),
        max_retries=int(max_retries),
        root=Path(overrides.get("root", root)),
        input_dir=Path(overrides.get("input_dir", input_dir)),
        output_dir=Path(overrides.get("output_dir", output_dir)),
        golden_dir=Path(overrides.get("golden_dir", golden_dir)),
        prompt_dir=_as_path(overrides.get("prompt_dir", prompt_dir)),
        probe_prompt_dir=_as_path(overrides.get("probe_prompt_dir", probe_prompt_dir)),
        presets_dir=_as_path(overrides.get("presets_dir", presets_dir)),
        normalizer_config=_as_path(overrides.get("normalizer_config", normalizer_config)),
        keep_runs=int(keep_runs),
        thresholds=thresholds,
        config_path=config_path,
        warnings=tuple(warnings),
    )
    validate_config(config)
    return config


def _as_path(value: Any) -> Path | None:
    return None if value is None else Path(value)


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"section [{name}] must be a table")
    return value


def _check_unknown_keys(raw: dict[str, Any], path: Path | None) -> list[str]:
    where = str(path) if path is not None else "config"
    warnings: list[str] = []
    for key in raw:
        if key not in _KNOWN_SECTIONS:
            warnings.append(f"{where}: ignoring unknown section [{key}]")
    for section, allowed in (
        ("ollama", _OLLAMA_KEYS),
        ("paths", _PATHS_KEYS),
        ("reports", _REPORTS_KEYS),
    ):
        table = raw.get(section)
        if not isinstance(table, dict):
            continue
        for key in table:
            if key not in allowed:
                warnings.append(f"{where}: ignoring unknown key [{section}].{key}")
    return warnings


def _typed(
    table: dict[str, Any],
    key: str,
    expected: type | tuple[type, ...],
    default: Any,
    name: str,
) -> Any:
    value = table.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ConfigError(f"{name} must be of type {expected}, got {type(value).__name__}")
    return value


def _string(table: dict[str, Any], key: str, default: str, name: str) -> str:
    value = _typed(table, key, str, default, name)
    if not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return str(value)


def _number(table: dict[str, Any], key: str, default: float, name: str) -> float:
    return float(_typed(table, key, (int, float), default, name))


def _integer(table: dict[str, Any], key: str, default: int, name: str) -> int:
    value = _typed(table, key, int, default, name)
    return int(value)


def _optional_int(table: dict[str, Any], key: str, default: int | None, name: str) -> int | None:
    if key not in table:
        return default
    value = table[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer or null, got {type(value).__name__}")
    return value


def _resolve_path(
    table: dict[str, Any],
    key: str,
    default: str,
    base_dir: Path,
    root: Path,
) -> Path:
    if key in table:
        raw = _typed(table, key, str, default, f"paths.{key}")
        anchor = base_dir
    else:
        raw = default
        anchor = root
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    return (anchor / candidate).resolve()


def _optional_path(table: dict[str, Any], key: str, base_dir: Path) -> Path | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"paths.{key} must be a non-empty string")
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def _load_thresholds(raw: dict[str, Any]) -> Thresholds:
    defaults = Thresholds().as_dict()
    unknown = sorted(set(raw) - set(defaults))
    if unknown:
        raise ConfigError("unknown threshold(s): " + ", ".join(unknown))
    values: dict[str, float] = {}
    for key, default in defaults.items():
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"thresholds.{key} must be a number")
        values[key] = float(value)
    return Thresholds(**values)


def _env_number(env: Mapping[str, str], names: tuple[str, ...], default: float) -> float:
    for name in names:
        if name in env and env[name] != "":
            try:
                return float(env[name])
            except ValueError as exc:
                raise ConfigError(f"{name} must be a number, got {env[name]!r}") from exc
    return default


def _env_integer(env: Mapping[str, str], names: tuple[str, ...], default: int) -> int:
    for name in names:
        if name in env and env[name] != "":
            try:
                return int(env[name])
            except ValueError as exc:
                raise ConfigError(f"{name} must be an integer, got {env[name]!r}") from exc
    return default


def _env_optional_int(
    env: Mapping[str, str], names: tuple[str, ...], default: int | None
) -> int | None:
    for name in names:
        if name not in env or env[name] == "":
            continue
        raw = env[name].strip().lower()
        if raw in {"none", "null"}:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer or 'none', got {env[name]!r}") from exc
    return default


def _env_path(env: Mapping[str, str], names: tuple[str, ...], default: Path) -> Path:
    for name in names:
        if name in env and env[name] != "":
            return Path(env[name]).expanduser()
    return default


def _env_optional_path(env: Mapping[str, str], name: str, default: Path | None) -> Path | None:
    if name in env and env[name] != "":
        return Path(env[name]).expanduser()
    return default


def validate_config(config: LabConfig) -> None:
    if not config.base_url.startswith(("http://", "https://")):
        raise ConfigError(f"base_url must start with http:// or https://: {config.base_url!r}")
    if config.base_url.endswith("/"):
        raise ConfigError("base_url must not end with '/'")
    if config.timeout <= 0:
        raise ConfigError("timeout must be > 0")
    if not 0.0 <= config.temperature <= 2.0:
        raise ConfigError("temperature must be in [0, 2]")
    if config.num_ctx < 512:
        raise ConfigError("num_ctx must be >= 512")
    if config.num_predict < 16:
        raise ConfigError("num_predict must be >= 16")
    if config.max_retries < 0:
        raise ConfigError("max_retries must be >= 0")
    if config.keep_runs < 1:
        raise ConfigError("keep_runs must be >= 1")
    if config.seed is not None and not isinstance(config.seed, int):
        raise ConfigError("seed must be an integer or null")
    for key, value in config.thresholds.as_dict().items():
        if key == "degradation_attempts_increase_max":
            if value < 0:
                raise ConfigError(f"{key} must be >= 0")
        elif not 0.0 <= value <= 1.0:
            raise ConfigError(f"{key} must be in [0, 1]")
