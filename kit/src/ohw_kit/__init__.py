"""ohw-kit: shared building blocks for the ohw homework projects."""

from ohw_kit.checks import CheckResult, evaluate, json_path
from ohw_kit.io import InputError, InputFile, load_input, register_reader
from ohw_kit.jsonreply import JsonReplyError, ValidationResult, extract_json
from ohw_kit.ollama_client import (
    DEFAULT_BASE_URL,
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from ohw_kit.render import render_markdown

__all__ = [
    "DEFAULT_BASE_URL",
    "CheckResult",
    "InputError",
    "InputFile",
    "JsonReplyError",
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaResponseError",
    "ValidationResult",
    "evaluate",
    "extract_json",
    "json_path",
    "load_input",
    "register_reader",
    "render_markdown",
]