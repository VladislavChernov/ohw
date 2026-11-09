"""Shared fixtures for the Dz8 unit and integration tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_degradation.config import LabConfig, load_config
from llm_degradation.models import NormalizedInput, PromptBundle
from llm_degradation.normalizer_bridge import build_prompt, load_normalized
from llm_degradation.ollama import ChatResponse, LabOllamaClient

ROOT = Path(__file__).resolve().parents[1]
DZ2_ROOT = ROOT.parent / "dz2"
GOLDEN = ROOT / "golden"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def dz2_root() -> Path:
    return DZ2_ROOT


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN


@pytest.fixture(scope="session")
def normalizer_config(dz2_root: Path) -> Path:
    return dz2_root / "normalizer.toml"


@pytest.fixture(scope="session")
def lab_config() -> LabConfig:
    return load_config(ROOT / "config.toml", env={})


@pytest.fixture(scope="session")
def prompt_dir(dz2_root: Path) -> Path:
    return dz2_root / "prompts"


@pytest.fixture(scope="session")
def probe_prompt_dir(repo_root: Path) -> Path:
    return repo_root / "prompts"


@pytest.fixture(scope="session")
def presets_dir(dz2_root: Path) -> Path:
    return dz2_root / "presets"


@pytest.fixture
def normalized(golden_dir: Path, normalizer_config: Path) -> NormalizedInput:
    return load_normalized(
        golden_dir / "inputs" / "no-cart.md", normalizer_config=normalizer_config
    )


@pytest.fixture
def catalog_normalized(golden_dir: Path, normalizer_config: Path) -> NormalizedInput:
    return load_normalized(
        golden_dir / "inputs" / "catalog.md", normalizer_config=normalizer_config
    )


@pytest.fixture
def prompt(normalized: NormalizedInput, prompt_dir: Path, presets_dir: Path) -> PromptBundle:
    from ai_testgen.models import ArtifactType

    return build_prompt(
        normalized,
        ArtifactType.TESTCASES,
        5,
        prompt_dir=prompt_dir,
        presets_dir=presets_dir,
    )


def read_recorded(name: str) -> dict[str, Any]:
    path = GOLDEN / "recorded" / name / "run-01.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def testcase_payload() -> str:
    return str(read_recorded("testcases")["response"]["message"]["content"])


@pytest.fixture(scope="session")
def canary_payload() -> str:
    return str(read_recorded("canary")["response"]["message"]["content"])


class SequenceClient:
    """Minimal chat client that returns canned answers in order."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, float, bool]] = []

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = False,
        **_: Any,
    ) -> str:
        self.calls.append((system, user, temperature, json_mode))
        if not self.answers:
            raise AssertionError("unexpected chat call")
        return self.answers.pop(0)

    def chat_messages(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        **_: Any,
    ) -> ChatResponse:
        text = messages[-1]["content"] if messages else ""
        self.calls.append((model or "", text, 0.0, False))
        if not self.answers:
            raise AssertionError("unexpected chat_messages call")
        return ChatResponse(model=model or "llama3", content=self.answers.pop(0), done=True)


@pytest.fixture
def sequence_client() -> Callable[[list[str]], SequenceClient]:
    return SequenceClient


class MockOllama:
    """Build a ``LabOllamaClient`` backed by ``httpx.MockTransport``."""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], Callable[[], httpx.Response]] = {}
        self.clients: list[LabOllamaClient] = []

    def json(self, method: str, path: str, payload: Any, status: int = 200) -> None:
        self.responses[(method.upper(), path)] = lambda: httpx.Response(status, json=payload)

    def text(
        self,
        method: str,
        path: str,
        body: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.responses[(method.upper(), path)] = lambda: httpx.Response(
            status, text=body, headers=headers
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = (request.method.upper(), request.url.path)
        if key not in self.responses:
            return httpx.Response(404, json={"error": f"no mock for {key}"})
        return self.responses[key]()

    def client(self, *, model: str = "llama3", timeout: float = 600.0) -> LabOllamaClient:
        transport = httpx.MockTransport(self.handler)
        client = LabOllamaClient(
            "http://mock-ollama:11434", model=model, timeout=timeout, transport=transport
        )
        self.clients.append(client)
        return client

    def close(self) -> None:
        for client in self.clients:
            client.close()


@pytest.fixture
def mock_ollama() -> Iterator[MockOllama]:
    mock = MockOllama()
    yield mock
    mock.close()
