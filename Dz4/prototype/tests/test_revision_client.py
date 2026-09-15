"""RevisionClient (ADR-026): окно полла, fail-open, пер-доменный кэш, счётчик ошибок.

Контракт: GET {INGESTION_URL}/api/v1/ingestion/revision?domain=<domain> ->
{revision, updated_at}. Ошибки поллера не молчат (rev.07 S6): инкремент
revision_poll_errors_total. REVISION_POLL_INTERVAL_S=0 -> поллер выключен (None).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from graphrag_proto.query_service import revision_client as rc
from graphrag_proto.query_service.revision_client import RevisionClient


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _stub_get(monkeypatch: pytest.MonkeyPatch, seq: list[dict[str, Any] | None]) -> Any:
    def fake(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 3.0) -> _Resp:
        fake.calls.append(params)
        entry = seq[min(len(fake.calls) - 1, len(seq) - 1)]
        if entry is None:
            raise rc.requests.RequestException("connection refused")
        return _Resp(entry)

    fake.calls = []
    monkeypatch.setattr(rc.requests, "get", fake)
    return fake


def test_revision_fetch_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _stub_get(
        monkeypatch,
        [
            {"revision": "abc", "updated_at": "2026-09-15T10:00:00Z"},
            {"revision": "def", "updated_at": "2026-09-15T10:00:01Z"},
        ],
    )
    client = RevisionClient("http://ing:8002", poll_interval_s=60.0)
    assert client.revision("it") == "abc"
    assert client.revision("it") == "abc"  # в окне -> из кэша, без второго GET
    assert len(fake.calls) == 1
    assert client.known_revisions() == {"it": "abc"}


def test_revision_per_domain_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _stub_get(
        monkeypatch,
        [
            {"revision": "abc", "updated_at": None},
            {"revision": "xyz", "updated_at": None},
        ],
    )
    client = RevisionClient("http://ing:8002", poll_interval_s=60.0)
    assert client.revision("it") == "abc"
    assert client.revision("cpp") == "xyz"
    assert len(fake.calls) == 2
    assert client.known_revisions() == {"it": "abc", "cpp": "xyz"}


def test_revision_repolls_after_window_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _stub_get(
        monkeypatch,
        [
            {"revision": "abc", "updated_at": None},
            {"revision": "def", "updated_at": None},
        ],
    )
    client = RevisionClient("http://ing:8002", poll_interval_s=0.05)
    assert client.revision("it") == "abc"
    time.sleep(0.06)  # окно истекло -> повторный GET
    assert client.revision("it") == "def"
    assert len(fake.calls) == 2


def test_revision_fail_open_last_known(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_get(
        monkeypatch,
        [
            {"revision": "abc", "updated_at": None},
            None,  # сбой полла
        ],
    )
    client = RevisionClient("http://ing:8002", poll_interval_s=0.05)
    assert client.revision("it") == "abc"
    time.sleep(0.06)
    assert client.revision("it") == "abc"  # fail-open: последняя известная
    assert client.revision_poll_errors_total == 1
    assert client.known_revisions() == {"it": "abc"}


def test_revision_fail_from_start_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_get(monkeypatch, [None])
    client = RevisionClient("http://ing:8002", poll_interval_s=5.0)
    assert client.revision("it") is None
    assert client.revision_poll_errors_total == 1


def test_revision_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _stub_get(monkeypatch, [{"revision": "abc", "updated_at": None}])
    client = RevisionClient("http://ing:8002", poll_interval_s=0.0)
    assert client.revision("it") is None
    assert not fake.calls  # поллер выключен — транспорта не трогаем


def test_revision_sends_domain_param(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _stub_get(monkeypatch, [{"revision": "abc", "updated_at": None}])
    client = RevisionClient("http://ing:8002", poll_interval_s=60.0)
    client.revision("cpp")
    assert fake.calls[0]["domain"] == "cpp"


@pytest.mark.parametrize(
    ("env", "expected_url", "expected_interval"),
    [
        ({}, "http://ingestion:8002", 5.0),
        ({"INGESTION_URL": "http://ing:9000"}, "http://ing:9000", 5.0),
        ({"INGESTION_URL": "http://ing:9000", "REVISION_POLL_INTERVAL_S": "0"}, "http://ing:9000", 0.0),
    ],
)
def test_from_env(monkeypatch: pytest.MonkeyPatch, env: dict, expected_url: str, expected_interval: float) -> None:
    for k in ("INGESTION_URL", "AUTH_API_KEY", "GRAPH_AUTH_API_KEY", "REVISION_POLL_INTERVAL_S", "REVISION_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    client = RevisionClient.from_env()
    assert client is not None
    assert client._base_url == expected_url.rstrip("/")
    assert client._poll_interval_s == expected_interval


def test_from_env_disabled_when_url_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGESTION_URL", " ")
    assert RevisionClient.from_env() is None