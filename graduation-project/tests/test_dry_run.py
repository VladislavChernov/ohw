"""Tests for prompt-only generation without Ollama."""

import hashlib
import json
from pathlib import Path

import pytest

from src.config import PROJECT_VERSION
from src.generator.models import ArtifactType
from src.generator.ollama_client import OllamaClient
from src.generator.pipeline import GenerationRequest, build_prompt_previews
from src.workflow import stages
from src.workflow.main import EXIT_USAGE, main


def _write_inputs(tmp_path: Path) -> Path:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "first.md").write_text(
        "BR-1. Система должна проверять форму. Ожидаем успешный результат.\n",
        encoding="utf-8",
    )
    (input_dir / "second.md").write_text(
        "BR-2. Система должна проверять пароль. Ожидаем сообщение об ошибке.\n",
        encoding="utf-8",
    )
    return input_dir


def _request(input_dir: Path, output_dir: Path) -> GenerationRequest:
    return GenerationRequest(
        input_dir=input_dir,
        artifact_type=ArtifactType.TESTCASES,
        count=1,
        output_dir=output_dir,
    )


def _patch_ollama_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "list_models",
        lambda self: ["qwen2.5:7b-instruct"],
    )

    def chat(
        self: OllamaClient,
        system: str,
        user: str,
        temperature: float,
        json_mode: bool = True,
    ) -> str:
        return json.dumps(
            {
                "cases": [
                    {
                        "id": "TC-01",
                        "type": "positive",
                        "title": "Успешный вход",
                        "steps": ["Открыть форму"],
                        "expected": "Форма открыта",
                        "requirement": "BR-1",
                    }
                ]
            }
        )

    monkeypatch.setattr(OllamaClient, "chat", chat)


def _patch_ollama_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Ollama must not be called in dry-run")

    monkeypatch.setattr(OllamaClient, "list_models", forbidden)
    monkeypatch.setattr(OllamaClient, "chat", forbidden)


def test_previews_have_prompts_and_hashes(tmp_path: Path) -> None:
    input_dir = _write_inputs(tmp_path)
    previews = build_prompt_previews(_request(input_dir, tmp_path / "prompts"))
    assert len(previews) == 2
    for preview in previews:
        assert preview.system_prompt
        assert "BR-" in preview.user_prompt
        assert preview.prompt_sha256 == hashlib.sha256(
            preview.user_prompt.encode()
        ).hexdigest()
        assert preview.note is None


def test_dry_run_prompt_matches_real_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = _write_inputs(tmp_path)
    _patch_ollama_succeeds(monkeypatch)

    previews = {
        preview.filename: preview
        for preview in build_prompt_previews(_request(input_dir, tmp_path / "dry"))
    }
    real_output = tmp_path / "real"
    assert (
        main(
            [
                "--generate",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(real_output),
                "--count",
                "1",
            ]
        )
        == 0
    )

    snapshot = json.loads(
        (real_output / "generation.json").read_text(encoding="utf-8")
    )
    for scenario in snapshot["scenarios"]:
        preview = previews[scenario["filename"]]
        assert scenario["prompt_sha256"] == preview.prompt_sha256
        assert scenario["prompt"] == preview.user_prompt


def test_dry_run_cli_without_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = _write_inputs(tmp_path)
    _patch_ollama_forbidden(monkeypatch)
    output_dir = tmp_path / "output"

    assert (
        main(
            [
                "--dry-run",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    assert not (output_dir / "generation.json").exists()
    assert not (output_dir / "scenarios").exists()
    manifest = json.loads(
        (output_dir / "prompts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == PROJECT_VERSION
    assert len(manifest["prompts"]) == 2
    for item in manifest["prompts"]:
        assert (output_dir / item["system_file"]).is_file()
        assert (output_dir / item["user_file"]).is_file()


def test_dry_run_conflicting_phases(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--dry-run", "--assess", "--output-dir", str(tmp_path)])
    assert exc.value.code == EXIT_USAGE


def test_coveragematrix_preview_has_note(tmp_path: Path) -> None:
    input_dir = _write_inputs(tmp_path)
    output_dir = tmp_path / "output"
    request = GenerationRequest(
        input_dir=input_dir,
        artifact_type=ArtifactType.COVERAGEMATRIX,
        count=1,
        output_dir=output_dir,
    )

    stages.dry_run(request, "qwen2.5:7b-instruct")
    manifest = json.loads(
        (output_dir / "prompts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert all(item["note"] for item in manifest["prompts"])


def test_dry_run_ignores_invalid_weights_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = _write_inputs(tmp_path)
    _patch_ollama_forbidden(monkeypatch)
    monkeypatch.setenv("GRADUATION_QUALITY_WEIGHTS", "bogus")

    assert (
        main(
            [
                "--dry-run",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )
        == 0
    )
