"""Тонкий HTTP/SSE-клиент демо-контура (add-demo-ui-e2e).

Без Streamlit: импортируется и юнит-тестируется. Контракты — M1/M2:
`POST /api/v1/ingestion/documents` (JSON `{source_url, domain, doc_type, content}`),
`GET /api/v1/ingestion/jobs/{job_id}`, `DELETE /api/v1/ingestion/documents`,
`POST /query` (`{query, metadata:{domain}}`), `GET /query/tasks/{task_id}/stream`
(SSE, конверт ADR-016), `DELETE /query/tasks/{task_id}`, список доменов Config Service.

Плюс операторское обслуживание: `POST /api/v1/maintenance/orphan-cleanup`
(`add-operator-maintenance-controls`). Планировщика нет намеренно — момент
запуска выбирает оператор.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import requests

from graphrag_proto.retrieval.adapters.base import MAX_EXPANSION_DEPTH

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:8002")
QUERY_URL = os.environ.get("QUERY_URL", "http://localhost:8000")
CONFIG_URL = os.environ.get("CONFIG_URL", "http://localhost:8001")
X_API_KEY = os.environ.get("X_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")

#: Границы ползунка глубины берутся из кода, а не пишутся в разметке: второе число в UI
#: означало бы второе место, где живёт потолок, и ровно то расхождение, которое ползунок
#: должен устранить.
DEPTH_MIN = 1
DEPTH_MAX = MAX_EXPANSION_DEPTH

#: Начальное положение ползунка. Это НЕ конфигурация: запрос из UI всегда несёт значение
#: явно, поэтому профиль не участвует. Авторитетное значение остаётся в профиле, а
#: ползунок показывает то, что реально применилось, из `effective_retrieval` ответа.
#: Расхождение с профилями ловит тест `test_slider_default_matches_profiles`.
DEFAULT_SLIDER_DEPTH = 3


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

    def run_maintenance(self, domain: str) -> dict[str, Any]:
        """Один проход уборки сиротских связей и узлов по домену.

        Маршрут сознателен без планировщика: уборка удаляет данные из графа, цена
        ошибки — молчаливая потеря, поэтому момент запуска выбирает оператор.

        **Форма ответа читается по коду, а не по имени:** `run_orphan_cleanup`
        возвращает `mode`, `skipped`, `planned_relations`, `removed_relations`,
        `planned_nodes`, `removed_nodes`, а маршрут добавляет `job_id` и `domain`.
        Счётчики раздельные с ADR-047; до него узлы попадали в `removed_relations`, а
        `removed_nodes` всегда был нулём. Поля `skipped`, `removed_relations` и
        `removed_nodes` обязательны для показа: без них «готово» неотличимо от
        «не разрешено» и «удалять нечего».
        """
        response = requests.post(
            f"{self._settings.ingestion_url}/api/v1/maintenance/orphan-cleanup",
            headers=self._headers,
            json={"domain": domain},
            timeout=120,
        )
        _raise_for_status(response)
        return _json_dict(response)

    def submit_query(self, query: str, domain: str, max_depth: int | None = None) -> str:
        """Отправить вопрос. `max_depth` — глубина обхода (ADR-036).

        `None` означает «не задавать»: поле не уходит в запрос, и глубину решает конфигурация
        (Config Service -> профиль -> кодовый фолбэк). Это не то же самое, что `0` или
        отсутствие слайдера, поэтому здесь именно `None`, и поле добавляется только когда
        значение задано.
        """
        payload: dict[str, Any] = {"query": query, "metadata": {"domain": domain}}
        if max_depth is not None:
            payload["max_depth"] = int(max_depth)
        response = requests.post(
            f"{self._settings.query_url}/query",
            headers=self._headers,
            json=payload,
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