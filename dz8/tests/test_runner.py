"""Series runner: N runs, retries, recording and persisted files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from ai_testgen.models import ArtifactType

from llm_degradation.models import NormalizedInput, PromptBundle
from llm_degradation.ollama import OllamaResponseError, OllamaUnavailable
from llm_degradation.runner import (
    MAX_RAW_BYTES,
    RecordingClient,
    RunnerError,
    SeriesOptions,
    run_series,
    source_urls,
)


def case(case_id: str, kind: str) -> dict[str, Any]:
    return {
        "id": case_id,
        "type": kind,
        "title": "Sample title",
        "steps": ["open the page"],
        "expected": "the page is shown",
    }


def valid_payload(count: int = 5) -> str:
    kinds = ["positive", "negative", "positive", "negative", "negative"]
    cases = [case(f"TC-{index + 1:02d}", kinds[index % len(kinds)]) for index in range(count)]
    return json.dumps({"cases": cases}, ensure_ascii=False)


class FakeLabClient:
    """Duck-typed ``LabOllamaClient`` returning queued answers."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = False,
        model: str | None = None,
        seed: int | None = 42,
        num_ctx: int = 4096,
        num_predict: int = 512,
    ) -> str:
        self.requests.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "json_mode": json_mode,
                "model": model,
                "seed": seed,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            }
        )
        if not self.answers:
            raise AssertionError("unexpected chat call")
        return self.answers.pop(0)


def run(
    client: FakeLabClient,
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
    *,
    runs: int = 1,
    max_retries: int = 0,
    progress: Any = None,
    model: str | None = None,
) -> list[Any]:
    return run_series(
        client,  # type: ignore[arg-type]
        normalized,
        prompt,
        artifact_type=ArtifactType.TESTCASES,
        count=5,
        options=SeriesOptions(
            runs=runs,
            temperature=0.2,
            seed=7,
            num_ctx=2048,
            num_predict=256,
            max_retries=max_retries,
        ),
        prompt_dir=prompt_dir,
        runs_dir=tmp_path,
        cell="llama3/t0.2",
        model=model,
        progress=progress,
    )


def test_n_runs_are_recorded(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient([valid_payload(), valid_payload()])
    records = run(client, normalized, prompt, prompt_dir, tmp_path, runs=2)

    assert [record.index for record in records] == [1, 2]
    assert all(record.validator_pass for record in records)
    assert all(record.attempts == 1 for record in records)
    assert all(record.cell == "llama3/t0.2" for record in records)
    assert all(record.error is None for record in records)
    assert len(client.requests) == 2


def test_options_are_forwarded_to_the_client(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient([valid_payload()])
    run(client, normalized, prompt, prompt_dir, tmp_path)
    request = client.requests[0]
    assert request["temperature"] == 0.2
    assert request["seed"] == 7
    assert request["num_ctx"] == 2048
    assert request["num_predict"] == 256
    assert request["json_mode"] is True
    assert request["system"]


def test_model_override_is_forwarded_to_the_client(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient([valid_payload(), valid_payload()])
    run(client, normalized, prompt, prompt_dir, tmp_path, runs=2, model="llama3:q4")
    assert [request["model"] for request in client.requests] == ["llama3:q4", "llama3:q4"]


def test_model_defaults_to_none_for_the_client_to_resolve(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient([valid_payload()])
    run(client, normalized, prompt, prompt_dir, tmp_path)
    assert client.requests[0]["model"] is None


def test_retry_uses_generate_artifact_loop(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient(["not json at all", valid_payload()])
    records = run(client, normalized, prompt, prompt_dir, tmp_path, max_retries=1)

    assert len(records) == 1
    assert records[0].attempts == 2
    assert records[0].validator_pass is True
    assert len(client.requests) == 2


def test_exhausted_retries_are_persisted_as_failure(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient(["still not json", "still not json"])
    records = run(client, normalized, prompt, prompt_dir, tmp_path, max_retries=1)

    record = records[0]
    assert record.attempts == 2
    assert record.validator_pass is False
    assert record.error is not None
    assert "could not produce valid testcases" in record.error
    assert record.metrics.validator_issues


def test_progress_callback_receives_every_run(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    seen: list[tuple[int, int, int]] = []

    def progress(index: int, total: int, record: Any) -> None:
        seen.append((index, total, record.index))

    client = FakeLabClient([valid_payload(), valid_payload(), valid_payload()])
    run(
        client,
        normalized,
        prompt,
        prompt_dir,
        tmp_path,
        runs=3,
        progress=progress,
    )
    assert seen == [(1, 3, 1), (2, 3, 2), (3, 3, 3)]


def test_run_files_are_written(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient([valid_payload()])
    records = run(client, normalized, prompt, prompt_dir, tmp_path)

    run_dir = tmp_path / "run-01"
    assert (run_dir / "request.json").is_file()
    assert (run_dir / "response.txt").is_file()
    assert (run_dir / "metrics.json").is_file()
    assert (run_dir / "run.json").is_file()

    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    assert request["attempts"][0]["index"] == 1
    assert request["messages"][0]["role"] == "system"

    persisted = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert persisted["index"] == records[0].index
    assert persisted["validator_pass"] is True

    response = (run_dir / "response.txt").read_text(encoding="utf-8")
    assert response == valid_payload()


def test_metrics_are_computed_from_the_last_attempt(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    client = FakeLabClient(["bad", valid_payload()])
    record = run(client, normalized, prompt, prompt_dir, tmp_path, max_retries=1)[0]
    assert record.raw_chars == len(valid_payload())
    assert record.metrics.tc_ids == ["TC-01", "TC-02", "TC-03", "TC-04", "TC-05"]


def test_missing_prompt_template_raises_runner_error(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    tmp_path: Path,
) -> None:
    with pytest.raises(RunnerError):
        run_series(
            FakeLabClient([]),  # type: ignore[arg-type]
            normalized,
            prompt,
            artifact_type=ArtifactType.TESTCASES,
            count=5,
            options=SeriesOptions(runs=1),
            prompt_dir=tmp_path,
        )


def test_oversized_raw_is_truncated(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
    tmp_path: Path,
) -> None:
    huge = "x" * (MAX_RAW_BYTES + 100)
    client = FakeLabClient([huge])
    record = run(client, normalized, prompt, prompt_dir, tmp_path, max_retries=0)[0]
    assert record.metrics.raw_chars <= MAX_RAW_BYTES
    assert record.metrics.truncated is True


def test_source_urls_include_normalized_url(
    normalized: NormalizedInput,
) -> None:
    urls = source_urls(normalized)
    assert normalized.url in urls
    assert len(urls) == len(set(urls))


class FailingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def chat(self, system: str, user: str, temperature: float, **_: Any) -> str:
        raise self.error


def test_recording_client_records_unavailable(
    normalized: NormalizedInput,
) -> None:
    recording = RecordingClient(
        FailingClient(OllamaUnavailable("down")),  # type: ignore[arg-type]
        seed=42,
        num_ctx=4096,
        num_predict=512,
    )
    with pytest.raises(OllamaUnavailable):
        recording.chat("system", "user", 0.7)
    assert len(recording.calls) == 1
    assert recording.calls[0].error == "ollama unavailable"
    assert recording.last is recording.calls[0]


def test_recording_client_records_response_error(
    normalized: NormalizedInput,
) -> None:
    recording = RecordingClient(
        FailingClient(OllamaResponseError("bad status")),  # type: ignore[arg-type]
        seed=None,
        num_ctx=1024,
        num_predict=64,
    )
    with pytest.raises(OllamaResponseError):
        recording.chat("system", "user", 0.1, json_mode=True)
    assert recording.calls[0].error == "bad status"
    assert recording.calls[0].json_mode is True
