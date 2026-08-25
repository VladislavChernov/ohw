import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ai_testgen.ollama_client import DEFAULT_BASE_URL

DEFAULT_TIMEOUT = 180.0
CONFIG_FILE_NAME = "ai-testgen.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    timeout: float = DEFAULT_TIMEOUT
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    temperature: float = 0.7
    max_retries: int = 3


def resolve_config_path(explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise ConfigError(f"config file not found: {explicit_path}")
        return explicit_path
    candidate = Path.cwd() / CONFIG_FILE_NAME
    return candidate if candidate.is_file() else None


def load_config(explicit_path: Path | None) -> Config:
    path = resolve_config_path(explicit_path)
    if path is None:
        return Config()

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc

    ollama = _section(raw, "ollama", path)
    paths = _section(raw, "paths", path)
    generation = _section(raw, "generation", path)

    return Config(
        base_url=_string(ollama, "base_url", DEFAULT_BASE_URL, path),
        model=_string(ollama, "model", "", path),
        timeout=_float(ollama, "timeout", DEFAULT_TIMEOUT, path),
        input_dir=Path(_string(paths, "input_dir", "input", path)),
        output_dir=Path(_string(paths, "output_dir", "output", path)),
        temperature=_float(generation, "temperature", 0.7, path),
        max_retries=_int(generation, "max_retries", 3, path),
    )


def _section(raw: dict, name: str, path: Path) -> dict:
    section = raw.get(name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: section [{name}] must be a table")
    unknown = set(section) - {
        "base_url",
        "model",
        "timeout",
        "input_dir",
        "output_dir",
        "temperature",
        "max_retries",
    }
    if unknown:
        print(f"warning: {path}: ignoring unknown keys in [{name}]: {', '.join(sorted(unknown))}", flush=True)
    return section


def _typed(
    section: dict,
    key: str,
    expected: type | tuple[type, ...],
    default: object,
    path: Path,
) -> object:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ConfigError(f"{path}: '{key}' must be scalar of expected type, got {type(value).__name__}")
    return value


def _string(section: dict, key: str, default: str, path: Path) -> str:
    return str(_typed(section, key, str, default, path))


def _float(section: dict, key: str, default: float, path: Path) -> float:
    return cast(float, _typed(section, key, (int, float), default, path))


def _int(section: dict, key: str, default: int, path: Path) -> int:
    return cast(int, _typed(section, key, int, default, path))
