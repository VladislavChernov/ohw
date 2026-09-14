"""E2e-тест семантического кэша (M3.3, бандл 3/3).

Запуск через run_demo_e2e.sh с SEMANTIC_CACHE_ENABLED=true.
Сценарий: запрос → cache_hit:false + token; повторный близкий → cache_hit:true без token.

Пропускается если SEMANTIC_CACHE_ENABLED не задан или кэш не включён на воркере.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from graphrag_proto.demo_ui.client import DemoClient

pytestmark = pytest.mark.e2e

DOMAIN = "doc"
SOURCE_URL = "s://e2e-cache/semantic-cache-test.txt"
DOC_TEXT = """\
Тестовый документ для проверки семантического кэша. Закупки в компании ведутся
через единый реестр поставщиков, обновляемый ежеквартально. Все счета-фактуры
сверяются с договорами до оплаты.
"""
QUESTION = "как ведутся закупки в компании?"


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
    body = envelope.get("payload")
    return body if isinstance(body, dict) else envelope


def _collect_stream(
    client: DemoClient, task_id: str, timeout_seconds: int = 300
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    done: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    tokens: list[str] = []
    statuses: list[dict[str, Any]] = []
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
            statuses.append(body)
    return done, error, tokens, statuses


@pytest.mark.skipif(
    os.environ.get("SEMANTIC_CACHE_ENABLED", "").lower() not in {"true", "1"},
    reason="Semantic cache не включён (SEMANTIC_CACHE_ENABLED не задан)",
)
def test_semantic_cache_hit_miss_e2e(e2e_client: DemoClient) -> None:
    accepted = e2e_client.upload_document(DOC_TEXT, "txt", DOMAIN, SOURCE_URL)
    assert accepted.get("status") == "queued"
    job = _wait_ingest(e2e_client, str(accepted["job_id"]))
    assert job.get("status") == "succeeded", job

    # --- miss ---
    task1 = e2e_client.submit_query(QUESTION, DOMAIN)
    done1, error1, tokens1, _ = _collect_stream(e2e_client, task1)
    assert error1 is None, error1
    assert done1 is not None
    assert done1.get("cache_hit") is False, f"Первый запрос должен быть cache_hit:false, got {done1}"
    assert len(tokens1) > 0, "Первый запрос (miss) должен породить token-события"

    # --- hit ---
    task2 = e2e_client.submit_query(QUESTION, DOMAIN)
    done2, error2, tokens2, _ = _collect_stream(e2e_client, task2)
    assert error2 is None, error2
    assert done2 is not None
    assert done2.get("cache_hit") is True, f"Повторный запрос должен быть cache_hit:true, got {done2}"
    assert len(tokens2) == 0, f"При hit token быть не должно, получено {len(tokens2)}: {tokens2}"
    assert done2.get("generation_time_s") == 0.0, "При hit generation_time_s должен быть 0"

    e2e_client.delete_document(DOMAIN, SOURCE_URL)
