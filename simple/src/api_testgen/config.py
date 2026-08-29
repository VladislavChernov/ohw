"""Configuration for API test generator."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    """App configuration loaded from environment."""

    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    target_url: str = "https://jsonplaceholder.typicode.com"
    output_dir: str = "./output"
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> Config:
        """Load config from environment variables."""
        return cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            target_url=os.getenv("TARGET_URL", cls.target_url),
            output_dir=os.getenv("OUTPUT_DIR", cls.output_dir),
            max_retries=int(os.getenv("MAX_RETRIES", str(cls.max_retries))),
        )
