"""Configuration for API test generator."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_REQUIRED_MARKERS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass
class Config:
    """App configuration loaded from environment."""

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    output_dir: str = "./output"
    max_retries: int = 3
    timeout: float = 600.0
    temperature: float = 0.3
    num_predict: int = 4096
    seed: int | None = None
    required_markers: list[str] = field(
        default_factory=lambda: list(DEFAULT_REQUIRED_MARKERS)
    )

    @classmethod
    def from_env(cls) -> Config:
        """Load config from environment variables.

        Default base URL is localhost (host run via `uv run`). Containers
        (devcontainer / docker compose app) override with OLLAMA_BASE_URL
        pointing to the shared ollama (host.docker.internal).
        """
        seed_env = os.getenv("OLLAMA_SEED")
        markers_env = os.getenv("REQUIRED_MARKERS")
        return cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            output_dir=os.getenv("OUTPUT_DIR", cls.output_dir),
            max_retries=int(os.getenv("MAX_RETRIES", str(cls.max_retries))),
            timeout=float(os.getenv("OLLAMA_TIMEOUT", str(cls.timeout))),
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", str(cls.temperature))),
            num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", str(cls.num_predict))),
            seed=int(seed_env) if seed_env else None,
            required_markers=(
                [m.strip() for m in markers_env.split(",") if m.strip()]
                if markers_env is not None
                else list(DEFAULT_REQUIRED_MARKERS)
            ),
        )
