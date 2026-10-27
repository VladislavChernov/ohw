"""Guard-тесты лаунчеров: Windows умеет CPU и NVIDIA GPU, macOS/Linux — только CPU.

Требование задания: `run.ps1` поднимает стенд в двух режимах, `run.sh` — только
на CPU. Проверяется инвариант ("не-Windows лаунчер не подключает GPU-оверлей"),
а не список флагов: переименование опции тест переживёт, а возврат GPU-режима в
`run.sh` — нет.

Сами скрипты в образ не копируются, поэтому в образе тесты пропускаются с
причиной; на смонтированном дереве они выполняются.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN_SH = ROOT / "run.sh"
RUN_PS1 = ROOT / "run.ps1"


def _read(path: pathlib.Path) -> str:
    if not path.exists():
        pytest.skip(f"{path.name} не копируется в образ; проверка идёт на хосте")
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> str:
    """Скрипт без комментариев: упоминание оверлея в пояснении — не его подключение."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_windows_launcher_offers_both_modes() -> None:
    text = _read(RUN_PS1)
    assert re.search(r"\[switch\]\$Gpu", text), "нет переключателя GPU-режима"
    assert "compose.gpu.yaml" in text, "GPU-режим не подключает CUDA-оверлей"
    assert '"nvidia"' in text, "нет проверки NVIDIA-рантайма перед запуском"
    assert "CPU" in text and "NVIDIA" in text, "выбранный режим не печатается"


def test_windows_launcher_uses_gpu_overlay_only_for_gpu_mode() -> None:
    text = _read(RUN_PS1)
    overlay_lines = [line for line in text.splitlines() if "compose.gpu.yaml" in line]
    assert len(overlay_lines) == 1, "оверлей должен подключаться в одном месте"
    assert "if ($Gpu)" in overlay_lines[0], "оверлей подключается безусловно"


def test_unix_launcher_is_cpu_only() -> None:
    text = _code_lines(_read(RUN_SH))
    assert "compose.gpu.yaml" not in text, "run.sh не должен подключать GPU-оверлей"
    assert "--gpu" not in text
    assert "--amd" not in text
    assert re.search(r'up\.sh"\s+"\$SCRIPT_DIR"', text), "ollama поднимается не через инфра-каталог"
    assert "CPU" in text, "CPU-режим не заявлен"
