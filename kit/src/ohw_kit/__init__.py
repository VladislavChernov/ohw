"""ohw-kit: shared building blocks for the ohw homework projects."""

from ohw_kit.io import InputError, InputFile, load_input, register_reader
from ohw_kit.ollama_client import (
    DEFAULT_BASE_URL,
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from ohw_kit.render import render_markdown

__all__ = [
    "DEFAULT_BASE_URL",
    "InputError",
    "InputFile",
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaResponseError",
    "load_input",
    "register_reader",
    "render_markdown",
]