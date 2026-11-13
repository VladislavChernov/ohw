"""Tests for per-file recovery in the generation phase (no network calls)."""

import json
from pathlib import Path

import pytest

from src.generator.models import ArtifactType
from src.generator.ollama_client import OllamaConnectionError
from src.generator.pipeline import GenerationRequest, generate_scenarios


def _response() -> str:
    return json.dumps(
        {
            "cases": [
                {
                    "id": "TC-01",
                    "type": "positive",
                    "title": "Успех",
                    "steps": ["Открыть форму"],
                    "expected": "Форма открыта",
                    "requirement": "BR-1",
                },
                {
                    "id": "TC-02",
                    "type": "negative",
                    "title": "Ошибка",
                    "steps": ["Открыть форму"],
                    "expected": "Показана ошибка",
                    "requirement": "BR-1",
                },
            ]
        },
        ensure_ascii=False,
    )


class _FlakyClient:
    """Fail on one requirement file and answer the other one."""

    model = "test-model"

    def __init__(self, fail_marker: str) -> None:
        self.fail_marker = fail_marker
        self.calls = 0

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = True,
    ) -> str:
        self.calls += 1
        if self.fail_marker in user:
            raise OllamaConnectionError("ollama service is unreachable")
        return _response()


def _request(tmp_path: Path, *, continue_on_error: bool) -> GenerationRequest:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "first.md").write_text(
        "BR-1. Проверка первого файла. Система должна работать.\n",
        encoding="utf-8",
    )
    (input_dir / "second.md").write_text(
        "BR-2. Проверка второго файла. Система должна работать.\n",
        encoding="utf-8",
    )
    return GenerationRequest(
        input_dir=input_dir,
        artifact_type=ArtifactType.TESTCASES,
        count=2,
        output_dir=tmp_path / "output",
        continue_on_error=continue_on_error,
    )


def test_connection_error_aborts_by_default(tmp_path: Path) -> None:
    request = _request(tmp_path, continue_on_error=False)
    client = _FlakyClient(fail_marker="первого файла")

    with pytest.raises(OllamaConnectionError):
        generate_scenarios(request, client)


def test_connection_error_is_per_file_when_enabled(tmp_path: Path) -> None:
    request = _request(tmp_path, continue_on_error=True)
    client = _FlakyClient(fail_marker="первого файла")

    outcome = generate_scenarios(request, client)

    assert [scenario.filename for scenario in outcome.scenarios] == ["second.md"]
    assert len(outcome.errors) == 1
    assert "first.md" in outcome.errors[0]
    assert "Ollama" in outcome.errors[0]
    assert (tmp_path / "output" / "scenarios" / "second.md").is_file()
