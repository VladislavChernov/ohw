"""Гард вкладки «Документы» после разведения ингеста и стенда (ADR-065).

Тот же приём, что в `test_demo_ui_maintenance.py`: разметка `app.py` не импортируется
ни одним тестом, поэтому проверяемо то, что можно вызвать без неё. Здесь это
`ingest_separated_lines` (текст слоя) и `DemoClient.ingestion_is_up` (проба живости).

Проверяется не «факт наличия текста», а содержательные требования: слой обязан сказать,
что это штатно, и дать РАБОТАЮЩУЮ команду для обеих платформ. Слой без команды
неполезен, а команда без пояснения выглядит как ошибка сборки.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from graphrag_proto.demo_ui.app import ingest_separated_lines
from graphrag_proto.demo_ui.client import DemoClient, Settings


def _texts(kind: str) -> list[str]:
    return [text for entry_kind, text in ingest_separated_lines() if entry_kind == kind]


def test_layer_says_separated_ingest_is_normal() -> None:
    """Слой обязан сказать, что это штатно, а не выглядеть поломкой."""
    notice = " ".join(_texts("notice"))
    assert "отдельный скрипт" in notice
    assert "ADR-065" in notice
    assert "штатно" in notice.lower()


def test_layer_gives_a_usable_command_for_both_platforms() -> None:
    """Команда обязана быть для Windows и для POSIX, и обе должны быть запускаемыми."""
    commands = _texts("command")
    assert any("ingest.ps1" in item and "up" in item for item in commands), (
        f"нет команды для Windows среди {commands}"
    )
    assert any("ingest.sh" in item and "up" in item for item in commands), (
        f"нет команды для Linux/macOS среди {commands}"
    )
    for item in commands:
        assert "Demo/" in item or "Demo\\" in item, (
            f"команда {item!r} названа без пути к скрипту и не выполнится из корня"
        )


def test_layer_points_at_the_path_without_ingest() -> None:
    """Обязан быть назван путь, который работает без ингеста, иначе человек застрянет."""
    captions = " ".join(_texts("caption"))
    assert "restore" in captions
    assert "down" in captions


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_probe_says_alive_only_on_a_healthy_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_get(url: str, **_kwargs: Any) -> _FakeResponse:
        seen.append(url)
        return _FakeResponse(200)

    monkeypatch.setattr(requests, "get", fake_get)
    client = DemoClient(Settings(ingestion_url="http://ingest:8002"))

    assert client.ingestion_is_up() is True
    assert seen == ["http://ingest:8002/health"]


def test_probe_reports_not_alive_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ сети - это «сервис не запущен», а не исключение на весь экран.

    Различие между «не запущен» и «не отвечает» UI не показывает, поэтому проба обязана
    сводить их к одному `False`, а не поднимать наружу то, что человек не может исправить.
    """

    def fake_get(_url: str, **_kwargs: Any) -> _FakeResponse:
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", fake_get)
    client = DemoClient(Settings(ingestion_url="http://ingest:8002"))

    assert client.ingestion_is_up() is False


def test_probe_treats_error_status_as_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(_url: str, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(503)

    monkeypatch.setattr(requests, "get", fake_get)
    client = DemoClient(Settings(ingestion_url="http://ingest:8002"))

    assert client.ingestion_is_up() is False