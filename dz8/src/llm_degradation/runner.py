"""Series runner: one prompt, N model runs, recorded on disk.

Retry policy is not duplicated: every run goes through
``ai_testgen.service.generate_artifact``, which owns the retry loop. The local
``RecordingClient`` only observes the calls so the lab can persist attempts.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_testgen.handlers import get_handler
from ai_testgen.input_doc import RequirementDoc
from ai_testgen.models import ArtifactType
from ai_testgen.prompt import PromptError
from ai_testgen.service import GenerationError, GenerationOptions, generate_artifact

from .metrics import artifact_metrics, required_sections_for
from .models import ArtifactMetrics, ChatCall, NormalizedInput, PromptBundle, RunRecord
from .ollama import LabOllamaClient, OllamaResponseError, OllamaUnavailable
from .util import iso_utc, write_json_atomic, write_text_atomic

MAX_RAW_BYTES = 1_000_000
URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


class RunnerError(Exception):
    """The handler could not be built (exit code 2)."""


@dataclass(frozen=True)
class SeriesOptions:
    runs: int = 10
    temperature: float = 0.7
    seed: int | None = 42
    num_ctx: int = 4096
    num_predict: int = 512
    max_retries: int = 3


class RecordingClient:
    """Delegates to ``LabOllamaClient`` and records every chat attempt."""

    def __init__(
        self,
        client: LabOllamaClient,
        *,
        seed: int | None,
        num_ctx: int,
        num_predict: int,
        model: str | None = None,
    ) -> None:
        self.client = client
        self.seed = seed
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.model = model
        self.calls: list[ChatCall] = []

    @property
    def last(self) -> ChatCall | None:
        return self.calls[-1] if self.calls else None

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        json_mode: bool = False,
    ) -> str:
        started = time.perf_counter()
        index = len(self.calls) + 1
        try:
            raw = self.client.chat(
                system,
                user,
                temperature,
                json_mode=json_mode,
                seed=self.seed,
                num_ctx=self.num_ctx,
                num_predict=self.num_predict,
                model=self.model,
            )
        except OllamaUnavailable:
            self.calls.append(
                ChatCall(
                    index=index,
                    system=system,
                    user=user,
                    temperature=temperature,
                    json_mode=json_mode,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error="ollama unavailable",
                )
            )
            raise
        except OllamaResponseError as exc:
            self.calls.append(
                ChatCall(
                    index=index,
                    system=system,
                    user=user,
                    temperature=temperature,
                    json_mode=json_mode,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    error=str(exc),
                )
            )
            raise
        self.calls.append(
            ChatCall(
                index=index,
                system=system,
                user=user,
                temperature=temperature,
                json_mode=json_mode,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                raw=raw,
            )
        )
        return raw


def source_urls(normalized: NormalizedInput) -> list[str]:
    urls = URL_PATTERN.findall(normalized.text)
    if normalized.url:
        urls.insert(0, normalized.url)
    return list(dict.fromkeys(urls))


def run_series(
    client: LabOllamaClient,
    normalized: NormalizedInput,
    prompt: PromptBundle,
    *,
    artifact_type: ArtifactType,
    count: int,
    options: SeriesOptions,
    prompt_dir: Path | None = None,
    runs_dir: Path | None = None,
    cell: str | None = None,
    model: str | None = None,
    progress: Callable[[int, int, RunRecord], None] | None = None,
) -> list[RunRecord]:
    """Run ``options.runs`` independent generation attempts and record them.

    ``model`` overrides ``client.model`` for every attempt, so the same client
    can be used for different cells (e.g. baseline vs candidate).
    """
    try:
        handler = get_handler(artifact_type, prompt_dir)
    except PromptError as exc:
        raise RunnerError(str(exc)) from exc

    doc = RequirementDoc(
        path=normalized.source,
        content=normalized.text,
        url=normalized.url,
        artifact_type=artifact_type,
    )
    generation_options = GenerationOptions(
        count=count,
        temperature=options.temperature,
        max_retries=options.max_retries,
    )
    urls = source_urls(normalized)
    records: list[RunRecord] = []

    for index in range(1, options.runs + 1):
        recording = RecordingClient(
            client,
            seed=options.seed,
            num_ctx=options.num_ctx,
            num_predict=options.num_predict,
            model=model,
        )
        started_at = iso_utc()
        started = time.perf_counter()
        passed = False
        attempts = 0
        error: str | None = None

        try:
            artifact = generate_artifact(
                recording,
                handler,
                doc,
                generation_options,
                user_prompt=prompt.user,
            )
            attempts = artifact.attempts
            passed = True
        except GenerationError as exc:
            attempts = exc.attempts
            error = str(exc)
            passed = False
        except OllamaResponseError as exc:
            attempts = len(recording.calls)
            error = str(exc)
            passed = False

        latency_ms = (time.perf_counter() - started) * 1000.0
        raw = recording.last.raw if recording.last is not None else ""
        oversized = len(raw.encode("utf-8")) > MAX_RAW_BYTES
        if oversized:
            raw = raw[:MAX_RAW_BYTES]

        metrics = artifact_metrics(
            artifact_type,
            raw,
            expected_count=count,
            source_br_ids=normalized.br_ids,
            source_urls=urls,
            required_sections=required_sections_for(artifact_type),
            source_text=normalized.text,
            id_prefix=normalized.settings.id_prefix,
        )
        updates: dict[str, Any] = {
            "latency_ms": round(latency_ms, 1),
            "attempts": attempts,
            "truncated": metrics.truncated or oversized,
        }
        if error:
            updates["validator_issues"] = [*metrics.validator_issues, error]
        metrics = _with_metrics(metrics, updates)

        record = RunRecord(
            index=index,
            cell=cell,
            started_at=started_at,
            latency_ms=round(latency_ms, 1),
            attempts=attempts,
            raw_sha256=metrics.raw_sha256,
            raw_chars=metrics.raw_chars,
            validator_pass=passed and metrics.validator_pass,
            metrics=metrics,
            error=error,
        )
        records.append(record)

        if runs_dir is not None:
            _write_run_files(runs_dir / f"run-{index:02d}", recording, raw, metrics, record)
        if progress is not None:
            progress(index, options.runs, record)

    return records


def _with_metrics(metrics: ArtifactMetrics, updates: dict[str, Any]) -> ArtifactMetrics:
    return metrics.model_copy(update=updates)


def _write_run_files(
    directory: Path,
    recording: RecordingClient,
    raw: str,
    metrics: ArtifactMetrics,
    record: RunRecord,
) -> None:
    last = recording.last
    request = {
        "messages": [
            {"role": "system", "content": last.system if last else ""},
            {"role": "user", "content": last.user if last else ""},
        ],
        "json_mode": last.json_mode if last else False,
        "temperature": last.temperature if last else 0.0,
        "attempts": [call.model_dump(mode="json") for call in recording.calls],
    }
    write_json_atomic(directory / "request.json", request)
    write_text_atomic(directory / "response.txt", raw)
    write_json_atomic(directory / "metrics.json", metrics.model_dump(mode="json"))
    write_json_atomic(directory / "run.json", record.model_dump(mode="json"))


def source_url_sequence(normalized: NormalizedInput) -> Sequence[str]:
    return source_urls(normalized)
