"""Время джобы видно: в ответе API и в логе.

Проверяется то, чего раньше не было видно вообще: длительности пишутся в базу на каждой
джобе с момента появления таблицы `job_stage_durations`, но наружу не отдавались, и оценка
«сколько стоит стадия» бралась на глаз.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from graphrag_proto.ingestion_service.app import create_app
from graphrag_proto.ingestion_service.storage.registry import JobStore
from tests.job_wait import wait_until

API_KEY = "changeme"
TEXT = "Document Registry построен поверх Glossary Service."


class _AuthedClient(TestClient):
    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-API-Key", API_KEY)
        return super().request(method, url, headers=headers, **kwargs)


def _app(tmp_path: Path):
    return create_app(
        upload_dir=tmp_path / "uploads",
        db_path=tmp_path / "ingestion.db",
        glossary_url="",
    )


def _load_and_wait(client: _AuthedClient, source_url: str) -> str:
    resp = client.post(
        "/api/v1/ingestion/documents",
        json={"source_url": source_url, "domain": "it", "doc_type": "txt", "content": TEXT},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert wait_until(
        lambda: client.get(f"/api/v1/ingestion/jobs/{job_id}").json().get("status")
        in {"succeeded", "failed"}
    )
    return job_id


def test_job_response_carries_stage_durations(tmp_path: Path) -> None:
    with _AuthedClient(_app(tmp_path)) as client:
        job_id = _load_and_wait(client, "src://algo.txt")
        body = client.get(f"/api/v1/ingestion/jobs/{job_id}").json()

    assert "stage_durations_ms" in body, "длительности обязаны быть видны по HTTP"
    assert body["stage_durations_ms"], "у прошедшей джобы длительности непусты"


def test_total_duration_is_the_sum_of_stages(tmp_path: Path) -> None:
    """Сумма рядом со стадиями: без неё «стадия 12 с» не отвечает на вопрос «сколько стоит
    документ целиком»."""
    with _AuthedClient(_app(tmp_path)) as client:
        job_id = _load_and_wait(client, "src://algo.txt")
        body = client.get(f"/api/v1/ingestion/jobs/{job_id}").json()

    assert body["total_duration_ms"] == sum(body["stage_durations_ms"].values())


def test_stage_durations_are_not_mistaken_for_zero(tmp_path: Path) -> None:
    """Пустая длительность значит «стадия не измерялась», а не «стадия заняла 0 мс».

    Состояние строится, а не выигрывается у конвейера (ADR-073): POST запускает `_run`
    в фоновом потоке до ответа `202`, поэтому «прочитать сразу после POST» проверяло
    скорость потока, а не контракт ответа, и падало в полном прогоне с `assert 23 == 0`.
    Строка джобы без строк длительностей — это и есть «ещё не начиналась».
    """
    db_path = tmp_path / "ingestion.db"
    with _AuthedClient(_app(tmp_path)) as client:
        JobStore(db_path).create(
            "job-not-started",
            source_url="src://algo.txt",
            domain="it",
            doc_type="txt",
        )
        body = client.get("/api/v1/ingestion/jobs/job-not-started").json()

    assert body["status"] == "queued"
    assert body["stage_durations_ms"] == {}
    assert body["total_duration_ms"] == 0, (
        "у ещё не начавшейся джобы сумма равна нулю - и это ноль измеренного, "
        "а не замер стадии"
    )
    assert "INGEST" not in body["stage_durations_ms"], (
        "стадия не должна выглядеть измеренной, пока её длительности нет"
    )


def test_completion_logs_total_and_slowest_stage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Время обязано попадать в лог, а не только в базу: по логу видно факт без запроса
    к API по каждой джобе."""
    caplog.set_level(logging.INFO, logger="graphrag_proto.ingestion_service.app")
    with _AuthedClient(_app(tmp_path)) as client:
        _load_and_wait(client, "src://algo.txt")

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "всего" in text and "мс" in text, f"в логе нет итогового времени:\n{text}"
    assert "самая долгая" in text, f"в логе нет самой долгой стадии:\n{text}"


def test_per_stage_times_are_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="graphrag_proto.ingestion_service.app")
    with _AuthedClient(_app(tmp_path)) as client:
        _load_and_wait(client, "src://algo.txt")

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "по стадиям" in text, f"в логе нет разбивки по стадиям:\n{text}"
    assert "INGEST=" in text, f"разбивка обязана называть стадии по именам:\n{text}"