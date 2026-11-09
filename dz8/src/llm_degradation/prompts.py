"""External prompt templates for dz8 probes.

Every text that is sent to the model (system prompts, user templates, probe
questions and API protocol messages) lives under ``dz8/prompts`` so it can be
reviewed and changed without touching Python code.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

PROMPT_DIR_NAME = "prompts"
PROMPT_DIR_ENV = "DZ8_PROMPT_DIR"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_DIR = PACKAGE_ROOT / PROMPT_DIR_NAME

_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}", re.IGNORECASE)


class PromptError(Exception):
    """Probe prompt template is missing or malformed."""


def resolve_prompt_dir(explicit: Path | None = None) -> Path:
    """Resolve the directory with dz8 prompt templates.

    Order: explicit value, ``DZ8_PROMPT_DIR``, ``./prompts``, packaged
    ``dz8/prompts``. Explicit values are strict: a wrong path is an error
    instead of a silent fallback.
    """
    if explicit is not None:
        return _require_dir(Path(explicit), "probe prompt dir")

    env_value = os.environ.get(PROMPT_DIR_ENV)
    if env_value:
        return _require_dir(Path(env_value), PROMPT_DIR_ENV)

    for candidate in (Path.cwd() / PROMPT_DIR_NAME, DEFAULT_PROMPT_DIR):
        if candidate.is_dir():
            return candidate

    raise PromptError(
        f"probe prompt directory not found: tried ./{PROMPT_DIR_NAME}, "
        f"{DEFAULT_PROMPT_DIR}; pass a path or set {PROMPT_DIR_ENV}"
    )


def _require_dir(path: Path, source: str) -> Path:
    if not path.is_dir():
        raise PromptError(f"probe prompt directory from {source} does not exist: {path}")
    return path


def load_prompt(name: str, prompt_dir: Path | None = None) -> str:
    """Read ``<prompt_dir>/<name>`` and drop the trailing newline."""
    path = resolve_prompt_dir(prompt_dir) / name
    if not path.is_file():
        raise PromptError(f"probe prompt template not found: {path}")
    try:
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise PromptError(f"cannot read probe prompt template {path}: {exc}") from exc


def render_prompt(template: str, values: Mapping[str, str]) -> str:
    """Substitute ``{name}`` placeholders in a probe prompt template."""
    lowered = {key.lower(): value for key, value in values.items()}
    names = {match.group(1).lower() for match in _PLACEHOLDER.finditer(template)}
    missing = sorted(name for name in names if name not in lowered)
    if missing:
        raise PromptError(
            "unknown placeholder(s): " + ", ".join(f"{{{name}}}" for name in missing)
        )
    return _PLACEHOLDER.sub(lambda match: lowered[match.group(1).lower()], template)
