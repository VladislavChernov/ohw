"""E2e-харнесс (marker `e2e`): полный цикл на живом стеке.

Запуск — через `infra/scripts/run_demo_e2e.sh` (сбрасывает volume'ы: предусловие
детерминизма при top_k=5 и хэш-эмбеддингах). В обычный `uv run pytest -q` не входит
(`addopts = "-m 'not e2e'"`).

Сценарий: upload txt -> INGEST succeeded -> query (done.sources содержит источник) ->
soft-delete -> следующий query уже без источника.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from graphrag_proto.demo_ui.client import DemoClient

pytestmark = pytest.mark.e2e

DOMAIN = "doc"
SOURCE_URL = "s://e2e/fin-director-brief.txt"
DOC_TEXT = """\
Краткая беседа с финансовым директором. НОСТРА отвечает за собираемость дебиторской
задолженности: контролирует сроки оплаты по договорам, управляет реестром дебиторов и
еженедельно передаёт отчёт о просроченной задолженности. Все эти задачи включены в
годовой план отдела финансового контроля.
"""
QUESTION = "за что НОСТРА отвечает перед финансовым директором?"
QUESTION_AFTER_DELETE = "какой отчёт передаёт НОСТРА по задолженности?"


def _wait_ingest(client: DemoClient, job_id: str, timeout_seconds: int = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        job = client.get_job(job_id)
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(1)
    raise AssertionError(f"INGEST {job_id} не завершился за {timeout_seconds} с: {job}")


def _event_body(envelope: dict[str, Any]) -> dict[str, Any]:
    """Тело события из SSE-конверта ADR-016 `{type, task_id, ts, payload}`."""
    body = envelope.get("payload")
    return body if isinstance(body, dict) else envelope


def _collect_stream(
    client: DemoClient, task_id: str, timeout_seconds: int = 300
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Дожидаемся терминального события SSE; возвращаем (done, error, tokens)."""
    deadline = time.monotonic() + timeout_seconds
    done: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    tokens: list[str] = []
    for event_type, envelope in client.stream_task(task_id):
        if time.monotonic() > deadline:
            raise AssertionError(f"SSE-стрим задачи {task_id} не завершился за {timeout_seconds} с")
        body = _event_body(envelope)
        if event_type == "done":
            done = body
        elif event_type == "error":
            error = body
        elif event_type == "token":
            tokens.append(str(body.get("text", "")))
        elif event_type == "status":
            continue
    return done, error, tokens


def _sources_of(done: dict[str, Any] | None) -> list[str]:
    if not done:
        return []
    sources = done.get("sources") or []
    return [str(source.get("source_url", "")) for source in sources if isinstance(source, dict)]


def test_demo_full_cycle(e2e_client: DemoClient) -> None:
    accepted = e2e_client.upload_document(DOC_TEXT, "txt", DOMAIN, SOURCE_URL)
    assert accepted.get("status") == "queued"
    job = _wait_ingest(e2e_client, str(accepted["job_id"]))
    assert job.get("status") == "succeeded", job

    task_id = e2e_client.submit_query(QUESTION, DOMAIN)
    done, error, _ = _collect_stream(e2e_client, task_id)
    assert error is None, error
    assert done is not None
    assert str(done.get("text", "")).strip(), "done.text пустой"
    assert SOURCE_URL in _sources_of(done), f"sources без загруженного источника: {_sources_of(done)}"

    e2e_client.delete_document(DOMAIN, SOURCE_URL)

    task_id_after = e2e_client.submit_query(QUESTION_AFTER_DELETE, DOMAIN)
    done_after, error_after, _ = _collect_stream(e2e_client, task_id_after)
    assert error_after is None, error_after
    assert done_after is not None
    assert SOURCE_URL not in _sources_of(done_after), (
        f"удалённый источник всё ещё в sources: {_sources_of(done_after)}"
    )