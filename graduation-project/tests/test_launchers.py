"""Guard test: the launchers keep the shared-infra contract.

The contract has three parts:

* ``run.ps1`` stays ASCII-only (Windows PowerShell 5.1 misparses UTF-8 without
  a BOM), starts ``ohw-ollama`` from ``../infra`` when it is not running, and
  offers an opt-in ``-Gpu`` mode with the shared CUDA overlay;
* with CLI arguments the launcher uses ``compose run``, without arguments it
  uses ``compose up --abort-on-container-exit``;
* ``run.cmd`` delegates to ``run.ps1`` and ``run.sh`` stays POSIX-shell valid
  and CPU-only, delegating infra startup to ``../infra/up.sh``.

The tests read the files as bytes and never start Docker.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN_PS1 = ROOT / "run.ps1"
RUN_CPU_PS1 = ROOT / "run-cpu.ps1"
RUN_GPU_PS1 = ROOT / "run-gpu.ps1"
RUN_CMD = ROOT / "run.cmd"
RUN_SH = ROOT / "run.sh"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> str:
    """Strip comments: an overlay mentioned in a comment is not connected."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_run_ps1_is_ascii_only() -> None:
    raw = RUN_PS1.read_bytes()
    non_ascii = [(index, byte) for index, byte in enumerate(raw) if byte > 127]
    assert not non_ascii, (
        "run.ps1 must stay ASCII-only for Windows PowerShell 5.1; "
        f"first non-ASCII bytes: {non_ascii[:5]}"
    )


def test_run_ps1_starts_shared_infra_and_waits_for_health() -> None:
    text = _text(RUN_PS1)
    assert "..\\infra\\compose.yaml" in text
    assert "'--profile', 'ollama'" in text
    assert "ohw-ollama" in text
    assert "State.Health" in text
    assert "healthy" in text


def test_run_ps1_passes_arguments_via_compose_run() -> None:
    text = _text(RUN_PS1)
    assert '"run", "--rm", "--build", "app"' in text
    assert '"--abort-on-container-exit"' in text
    assert '"--exit-code-from", "app"' in text


def test_windows_wrappers_delegate_to_run_ps1() -> None:
    cpu_text = _text(RUN_CPU_PS1)
    gpu_text = _text(RUN_GPU_PS1)
    cpu = _code_lines(cpu_text.split("#>", maxsplit=1)[-1])
    gpu = _code_lines(gpu_text.split("#>", maxsplit=1)[-1])
    assert "run.ps1" in cpu and "run.ps1" in gpu
    assert "@args" in cpu or "run.ps1" in cpu
    assert "run.ps1" in gpu
    assert "Where-Object" in gpu, "GPU wrapper must de-duplicate -Gpu"
    assert "does not accept -Gpu" in cpu, "CPU wrapper must reject -Gpu"
    for path in (RUN_CPU_PS1, RUN_GPU_PS1):
        raw = path.read_bytes()
        assert not [byte for byte in raw if byte > 127], f"{path.name} must be ASCII"
        body = _text(path).split("#>", maxsplit=1)[-1]
        assert _code_lines(body).count("run.ps1") == 1
        assert "compose.gpu.yaml" not in body, "overlays belong to run.ps1 only"


def test_gpu_wrapper_forwards_explicit_flags() -> None:
    body = _code_lines(_text(RUN_GPU_PS1).split("#>", maxsplit=1)[-1])
    assert "-Gpu @forward" in body, "GPU mode must be passed to run.ps1"

    cpu_body = _code_lines(_text(RUN_CPU_PS1).split("#>", maxsplit=1)[-1])
    assert "-Gpu @args" not in cpu_body, "CPU wrapper must not enable GPU mode"


def test_run_cmd_delegates_to_powershell_launcher() -> None:
    text = _text(RUN_CMD)
    assert "run.ps1" in text
    assert "%*" in text
    assert "%ERRORLEVEL%" in text


def test_run_ps1_offers_gpu_mode() -> None:
    text = _text(RUN_PS1)
    assert "[switch]$Gpu" in text, "нет переключателя GPU-режима"
    assert "compose.gpu.yaml" in text, "GPU-режим не подключает CUDA-оверлей"
    assert '"nvidia"' in text, "нет проверки nvidia-рантайма"
    assert "NVIDIA GPU" in text and "CPU" in text, "режим не печатается"


def test_run_ps1_uses_gpu_overlay_only_in_gpu_mode() -> None:
    overlay_lines = [
        line
        for line in _code_lines(_text(RUN_PS1)).splitlines()
        if "compose.gpu.yaml" in line
    ]
    assert len(overlay_lines) == 1, "оверлей должен подключаться в одном месте"
    assert "if ($Gpu)" in overlay_lines[0], "оверлей подключается безусловно"


def test_run_sh_starts_shared_infra_and_passes_arguments() -> None:
    text = _code_lines(_text(RUN_SH))
    assert "bash ../infra/up.sh ." in text
    assert "ohw-ollama" in text
    assert "docker compose run" in text
    assert "docker compose up" in text


def test_run_sh_is_cpu_only() -> None:
    text = _code_lines(_text(RUN_SH))
    assert "compose.gpu.yaml" not in text
    assert "--gpu" not in text
    assert "--amd" not in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is not available")
def test_run_sh_has_valid_shell_syntax() -> None:
    result = subprocess.run(
        ["sh", "-n", str(RUN_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
