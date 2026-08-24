import json
from pathlib import Path

import pytest

import ai_testgen.cli as cli_module
from ai_testgen.models import CaseType


class FakeChat:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []
        self.temps: list[float] = []
        self.json_modes: list[bool] = []

    def __call__(self, system: str, prompt: str, temperature: float, *, json_mode: bool = True) -> str:
        self.calls.append(prompt)
        self.temps.append(temperature)
        self.json_modes.append(json_mode)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def patch_client(monkeypatch: pytest.MonkeyPatch, fake) -> None:
    monkeypatch.setattr(cli_module.OllamaClient, "chat", staticmethod(fake))


def capture_client_init(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}
    real_init = cli_module.OllamaClient.__init__

    def spy_init(self, *, base_url="", model="", timeout=180.0):
        captured.update(base_url=base_url, model=model, timeout=timeout)
        real_init(self, base_url=base_url, model=model, timeout=timeout)

    monkeypatch.setattr(cli_module.OllamaClient, "__init__", spy_init)
    return captured


def write_input(dir_path: Path, name: str, text: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / name
    file_path.write_text(text, encoding="utf-8")
    return file_path


def make_case(
    id: str = "TC-01",
    type: CaseType = CaseType.POSITIVE,
    steps: list[str] | None = None,
) -> dict:
    return {
        "id": id,
        "type": type.value,
        "title": f"title {id}",
        "preconditions": ["site is available"],
        "steps": ["open page", "check element"] if steps is None else steps,
        "expected": "expected result",
    }


def make_raw_cases(cases: list[dict]) -> str:
    return json.dumps(cases)


def balanced_set(count: int) -> list[dict]:
    negatives = 0 if count < 2 else max(1, -(-count * 2 // 5))
    cases = []
    for i in range(count):
        case_type = CaseType.NEGATIVE if i < negatives else CaseType.POSITIVE
        cases.append(make_case(id=f"TC-{i + 1:02d}", type=case_type))
    return cases


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def input_dir(tmp_path: Path) -> Path:
    return tmp_path / "input"


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "output"
