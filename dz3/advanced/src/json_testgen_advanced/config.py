"""Advanced configuration loaded from environment / CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ohw_kit.ollama_client import DEFAULT_BASE_URL

DEFAULT_REQUIRED_RESOURCES = ["posts", "comments", "albums", "photos", "todos", "users"]


@dataclass
class AdvancedConfig:
    """Runtime configuration for the advanced generator."""

    ollama_base_url: str = DEFAULT_BASE_URL
    ollama_model: str = "qwen2.5:7b-instruct"
    service: str = "https://jsonplaceholder.typicode.com"
    input_dir: str = "./input"
    contracts_dir: str = "./input/contracts"
    output_dir: str = "./output"
    max_retries: int = 3
    timeout: float = 300.0
    temperature: float = 0.3
    json_mode: bool = True
    required_resources: list[str] = field(
        default_factory=lambda: list(DEFAULT_REQUIRED_RESOURCES)
    )

    @classmethod
    def from_env(cls) -> AdvancedConfig:
        """Load config from environment, mirroring the simple variant."""
        resources = os.getenv("REQUIRED_RESOURCES")
        cfg = cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", DEFAULT_BASE_URL),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            service=os.getenv("API_SERVICE", cls.service),
            input_dir=os.getenv("INPUT_DIR", cls.input_dir),
            contracts_dir=os.getenv("CONTRACTS_DIR", cls.contracts_dir),
            output_dir=os.getenv("OUTPUT_DIR", cls.output_dir),
            max_retries=int(os.getenv("MAX_RETRIES", str(cls.max_retries))),
            timeout=float(os.getenv("OLLAMA_TIMEOUT", str(cls.timeout))),
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", str(cls.temperature))),
            required_resources=(
                [r.strip() for r in resources.split(",") if r.strip()]
                if resources is not None
                else list(DEFAULT_REQUIRED_RESOURCES)
            ),
        )
        return cfg
