"""Тонкий HTTP/SSE-клиент демо-контура (add-demo-ui-e2e).

Без Streamlit: импортируется и юнит-тестируется. Контракты — M1/M2:
`POST /api/v1/ingestion/documents` (JSON `{source_url, domain, doc_type, content}`),
`GET /api/v1/ingestion/jobs/{job_id}`, `DELETE /api/v1/ingestion/documents`,
`POST /query` (`{query, metadata:{domain}}`), `GET /query/tasks/{task_id}/stream`
(SSE, конверт ADR-016), `DELETE /query/tasks/{task_id}`, список доменов Config Service.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import requests

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:8002")
QUERY_URL = os.environ.get("QUERY_URL", "http://localhost:8000")
CONFIG_URL = os.environ.get("CONFIG_URL", "http://localhost:8001")
X_API_KEY = os.environ.get("X_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")


@dataclass(frozen=True)
class Settings:
    ingestion_url: str = INGESTION_URL
    query_url: str = QUERY_URL
    config_url: str = CONFIG_URL
    api_key: str = X_API_KEY


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


def _raise_for_status(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    detail: Any = None
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    message = detail if isinstance(detail, str) and detail else (response.text or "ошибка HTTP")
    raise RuntimeError(f"HTTP {response.status_code}: {message}")


def _json_dict(response: requests.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return body


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Разбор SSE-потока (конверт ADR-016): `event:`/`data:` → (type, payload)."""
    event_type: str | None = None
    data_lines: list[str] = []
    for raw in lines:
        if raw is None:
            continue
        line = raw.rstrip("\n")
        if not line:
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                yield (event_type or "message"), payload
                event_type = None
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())


class DemoClient:
    """Обёртка над REST/SSE-эндпоинтами с X-API-Key и читаемыми ошибками."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._headers = _headers(self._settings.api_key)

    def list_domains(self) -> list[str]:
        response = requests.get(
            f"{self._settings.config_url}/api/v1/config/domain/profiles",
            headers=self._headers,
            timeout=5,
        )
        _raise_for_status(response)
        domains = response.json().get("domains")
        return domains if isinstance(domains, list) else []

    def upload_document(self, content: str, doc_type: str, domain: str, source_url: str) -> dict[str, Any]:
        response = requests.post(
            f"{self._settings.ingestion_url}/api/v1/ingestion/documents",
            headers=self._headers,
            json={
                "source_url": source_url,
                "domain": domain,
                "doc_type": doc_type,
                "content": content,
            },
            timeout=30,
        )
        _raise_for_status(response)
        return _json_dict(response)

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self._settings.ingestion_url}/api/v1/ingestion/jobs/{job_id}",
            headers=self._headers,
            timeout=10,
        )
        _raise_for_status(response)
        return _json_dict(response)

    def delete_document(self, domain: str, source_url: str) -> dict[str, Any]:
        response = requests.delete(
            f"{self._settings.ingestion_url}/api/v1/ingestion/documents",
            headers=self._headers,
            params={"domain": domain, "source_url": source_url},
            timeout=10,
        )
        _raise_for_status(response)
        return _json_dict(response)

    def submit_query(self, query: str, domain: str) -> str:
        response = requests.post(
            f"{self._settings.query_url}/query",
            headers=self._headers,
            json={"query": query, "metadata": {"domain": domain}},
            timeout=10,
        )
        _raise_for_status(response)
        task_id = response.json().get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError("ответ /query не содержит task_id")
        return task_id

    def task_status(self, task_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self._settings.query_url}/query/tasks/{task_id}",
            headers=self._headers,
            timeout=10,
        )
        _raise_for_status(response)
        return _json_dict(response)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        response = requests.delete(
            f"{self._settings.query_url}/query/tasks/{task_id}",
            headers=self._headers,
            timeout=10,
        )
        _raise_for_status(response)
        return _json_dict(response)

    def stream_task(self, task_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
        response = requests.get(
            f"{self._settings.query_url}/query/tasks/{task_id}/stream",
            headers=self._headers,
            stream=True,
            timeout=(10, 300),
        )
        with response:
            _raise_for_status(response)
            yield from parse_sse(response.iter_lines(decode_unicode=True))