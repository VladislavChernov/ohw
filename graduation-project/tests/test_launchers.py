"""Guard test: the launchers keep the shared-infra contract.

The contract has three parts:

* ``run.ps1`` stays ASCII-only (Windows PowerShell 5.1 misparses UTF-8 without
  a BOM) and starts ``ohw-ollama`` from ``../infra`` when it is not running;
* with CLI arguments the launcher uses ``compose run``, without arguments it
  uses ``compose up --abort-on-container-exit``;
* ``run.cmd`` delegates to ``run.ps1`` and ``run.sh`` stays POSIX-shell valid.

The tests read the files as bytes and never start Docker.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN_PS1 = ROOT / "run.ps1"
RUN_CMD = ROOT / "run.cmd"
RUN_SH = ROOT / "run.sh"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


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
    assert "--profile ollama" in text
    assert "ohw-ollama" in text
    assert "State.Health" in text
    assert "healthy" in text


def test_run_ps1_passes_arguments_via_compose_run() -> None:
    text = _text(RUN_PS1)
    assert '"run", "--rm", "--build", "app"' in text
    assert '"--abort-on-container-exit"' in text
    assert '"--exit-code-from", "app"' in text


def test_run_cmd_delegates_to_powershell_launcher() -> None:
    text = _text(RUN_CMD)
    assert "run.ps1" in text
    assert "%*" in text
    assert "%ERRORLEVEL%" in text


def test_run_sh_starts_shared_infra_and_passes_arguments() -> None:
    text = _text(RUN_SH)
    assert "../infra/compose.yaml" in text
    assert "--profile ollama" in text
    assert "ohw-ollama" in text
    assert "docker compose run" in text
    assert "docker compose up" in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is not available")
def test_run_sh_has_valid_shell_syntax() -> None:
    result = subprocess.run(
        ["sh", "-n", str(RUN_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
