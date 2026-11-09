"""CLI dispatch, exit codes and report commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_degradation import cli
from llm_degradation.config import ConfigError
from llm_degradation.ollama import ChatResponse
from llm_degradation.report import REPORT_JSON


def payload() -> str:
    cases = [
        {
            "id": f"TC-0{index + 1}",
            "type": "negative" if index >= 2 else "positive",
            "title": "Sample",
            "steps": ["step"],
            "expected": "result",
            "requirement": f"BR-{min(index + 1, 4)}",
        }
        for index in range(5)
    ]
    return json.dumps({"cases": cases})


class FakeStandClient:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.healthy = True
        self.chat_answer = payload()
        self.text_answer = "4"
        self.echo_document = False

    def close(self) -> None:
        pass

    def health(self, model: str | None = None) -> dict[str, Any]:
        if not self.healthy:
            return {
                "ok": False,
                "error": "model 'llama3' is not installed",
                "command_hint": "docker exec ohw-ollama ollama pull llama3",
            }
        return {
            "ok": True,
            "ollama_version": "0.9.1",
            "digest": "abc",
            "models": ["llama3:latest"],
            "command_hint": None,
        }

    def chat(self, system: str, user: str, temperature: float, **_: Any) -> str:
        return self.chat_answer

    def chat_messages(self, messages: list[dict[str, str]], **kwargs: Any) -> ChatResponse:
        content = messages[-1]["content"] if messages else ""
        if self.echo_document:
            start = content.index("=== ДОКУМЕНТ ===\n") + len("=== ДОКУМЕНТ ===\n")
            end = content.index("\n=== КОНЕЦ ДОКУМЕНТА ===")
            content = content[start:end]
            return ChatResponse(model="llama3", content=content, done=True)
        return ChatResponse(
            model=kwargs.get("model", "llama3"), content=self.text_answer, done=True
        )


@pytest.fixture
def stand(monkeypatch: pytest.MonkeyPatch) -> FakeStandClient:
    client = FakeStandClient()
    monkeypatch.setattr(cli, "LabOllamaClient", lambda *_a, **_k: client)
    return client


def write_config(tmp_path: Path, golden_dir: Path, dz2_root: Path) -> Path:
    body = f"""
[ollama]
model = "llama3"
max_retries = 0
seed = 42

[paths]
input_dir = "{(golden_dir / "inputs").as_posix()}"
output_dir = "{(tmp_path / "out").as_posix()}"
golden_dir = "{golden_dir.as_posix()}"
prompt_dir = "{(dz2_root / "prompts").as_posix()}"
presets_dir = "{(dz2_root / "presets").as_posix()}"
normalizer_config = "{(dz2_root / "normalizer.toml").as_posix()}"
"""
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "llm-degradation-lab" in capsys.readouterr().out


def test_missing_command_is_usage_error() -> None:
    assert cli.main([]) == cli.EXIT_USAGE


def test_unknown_command_is_usage_error() -> None:
    assert cli.main(["nope"]) == cli.EXIT_USAGE


def test_unknown_artifact_type_is_usage_error(tmp_path: Path) -> None:
    assert cli.main(["consistency", "--artifact-type", "wrong"]) == cli.EXIT_USAGE


def test_parse_collects_common_and_specific_flags() -> None:
    args = cli._parse(
        [
            "consistency",
            "--model",
            "llama3",
            "--runs",
            "3",
            "--count",
            "7",
            "--json",
        ]
    )
    assert args.command == "consistency"
    assert args.model == "llama3"
    assert args.runs == 3
    assert args.count == 7
    assert args.json is True


def test_series_options_rejects_zero_runs() -> None:
    args = cli._parse(["consistency", "--runs", "0"])
    with pytest.raises(ConfigError, match="runs must be >= 1"):
        cli._series_options(args, cli.LabConfig(), default_runs=10)


def test_normalize_prints_json(
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    code = cli.main(
        [
            "normalize",
            "--config",
            str(config),
            "--input",
            str(golden_dir / "inputs" / "no-cart.md"),
            "--json",
        ]
    )
    assert code == cli.EXIT_OK
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["br_ids"] == ["BR-1", "BR-2", "BR-3", "BR-4"]
    assert output["prompt_sha256"]


def test_normalize_writes_canonical_text(
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    target = tmp_path / "normalized.md"
    code = cli.main(
        [
            "normalize",
            "--config",
            str(config),
            "--input",
            str(golden_dir / "inputs" / "no-cart.md"),
            "--write",
            str(target),
        ]
    )
    assert code == cli.EXIT_OK
    assert target.read_text(encoding="utf-8").startswith("#")


def test_missing_input_returns_usage_error(
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    code = cli.main(
        [
            "normalize",
            "--config",
            str(config),
            "--input",
            str(tmp_path / "missing.md"),
            "--json",
        ]
    )
    assert code == cli.EXIT_USAGE
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["error"]["code"] == cli.EXIT_USAGE


def test_health_ok(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    code = cli.main(["health", "--config", str(config), "--json"])
    assert code == cli.EXIT_OK
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["digest"] == "abc"


def test_health_missing_model_returns_ollama_error(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stand.healthy = False
    config = write_config(tmp_path, golden_dir, dz2_root)
    code = cli.main(["health", "--config", str(config), "--json"])
    assert code == cli.EXIT_OLLAMA
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["command_hint"]


def test_consistency_writes_report(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    code = cli.main(
        [
            "consistency",
            "--config",
            str(config),
            "--input",
            str(golden_dir / "inputs" / "no-cart.md"),
            "--runs",
            "2",
            "--json",
            "--quiet",
        ]
    )
    assert code == cli.EXIT_OK
    output = json.loads(capsys.readouterr().out)
    assert output["verdict"] == "pass"
    report_path = Path(output["report_path"])
    assert report_path.is_file()
    assert report_path.name == REPORT_JSON


def test_canary_passes_when_llm_matches_normalizer(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
) -> None:
    stand.echo_document = True
    config = write_config(tmp_path, golden_dir, dz2_root)
    code = cli.main(
        [
            "canary",
            "--config",
            str(config),
            "--runs",
            "1",
            "--json",
            "--quiet",
        ]
    )
    assert code == cli.EXIT_OK
    raw_files = list((tmp_path / "out").glob("*/raw/canary/run-01.txt"))
    assert len(raw_files) == 1
    assert raw_files[0].read_text(encoding="utf-8").strip()


def make_probes(tmp_path: Path, input_path: Path, *, critical: bool = True) -> Path:
    path = tmp_path / "probes.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "probes": [
                    {
                        "id": "H-01",
                        "kind": "count",
                        "input": str(input_path),
                        "expected": {"number": 4},
                        "critical": critical,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_hallucinations_pass(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    probes = make_probes(tmp_path, golden_dir / "inputs" / "no-cart.md")
    code = cli.main(
        [
            "hallucinations",
            "--config",
            str(config),
            "--probes",
            str(probes),
            "--runs",
            "1",
            "--json",
            "--quiet",
        ]
    )
    assert code == cli.EXIT_OK


def test_hallucinations_fail_returns_check_error(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
) -> None:
    stand.text_answer = "5"
    config = write_config(tmp_path, golden_dir, dz2_root)
    probes = make_probes(tmp_path, golden_dir / "inputs" / "no-cart.md")
    code = cli.main(
        [
            "hallucinations",
            "--config",
            str(config),
            "--probes",
            str(probes),
            "--runs",
            "1",
            "--json",
            "--quiet",
        ]
    )
    assert code == cli.EXIT_CHECK


def test_hallucinations_strict_fail_returns_regression(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
) -> None:
    stand.text_answer = "5"
    config = write_config(tmp_path, golden_dir, dz2_root)
    probes = make_probes(tmp_path, golden_dir / "inputs" / "no-cart.md")
    code = cli.main(
        [
            "hallucinations",
            "--config",
            str(config),
            "--probes",
            str(probes),
            "--runs",
            "1",
            "--strict",
            "--json",
            "--quiet",
        ]
    )
    assert code == cli.EXIT_REGRESSION


def test_report_list_and_read(
    stand: FakeStandClient,
    tmp_path: Path,
    golden_dir: Path,
    dz2_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    cli.main(
        [
            "consistency",
            "--config",
            str(config),
            "--input",
            str(golden_dir / "inputs" / "no-cart.md"),
            "--runs",
            "1",
            "--json",
            "--quiet",
        ]
    )
    capsys.readouterr()

    assert cli.main(["report", "--config", str(config), "--list", "--json"]) == cli.EXIT_OK
    listing = json.loads(capsys.readouterr().out)
    assert len(listing["reports"]) == 1
    run_id = listing["reports"][0]["run_id"]

    assert (
        cli.main(["report", "--config", str(config), "--run-id", run_id, "--format", "json"])
        == cli.EXIT_OK
    )
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] == run_id


def test_report_requires_path_or_run_id(tmp_path: Path, golden_dir: Path, dz2_root: Path) -> None:
    config = write_config(tmp_path, golden_dir, dz2_root)
    assert cli.main(["report", "--config", str(config)]) == cli.EXIT_USAGE


def test_emit_error_json_payload(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli._parse(["health", "--json"])
    code = cli._emit_error(args, cli.EXIT_USAGE, "bad input", "hint")
    assert code == cli.EXIT_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["message"] == "bad input"
    assert payload["error"]["hint"] == "hint"
