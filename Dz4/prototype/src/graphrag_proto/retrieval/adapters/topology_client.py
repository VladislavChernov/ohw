"""Тонкий HTTP-клиент к Topology Orchestrator (:8005) — add-topology-adapters.

Отдаёт карту адаптеров и её revision для потребителей (Query Worker). Недоступность
топологии — не ошибка: потребитель фоллбэчит на env (M2-поведение). Контракт:
`GET /api/v1/config/adapters` -> `{revision, adapters: {slot: provider}}`.
"""

from __future__ import annotations

import threading
from typing import Any

import requests


class TopologyClient:
    """Кэширующая обёртка над GET /api/v1/config/adapters (thread-safe)."""

    def __init__(self, base_url: str, api_key: str = "changeme", timeout_s: float = 3.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._revision: int | None = None
        self._adapters: dict[str, str] | None = None

    @classmethod
    def from_env(cls) -> TopologyClient | None:
        import os

        url = os.environ.get("TOPOLOGY_URL")
        if not url:
            return None
        key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
        return cls(
            url,
            api_key=key,
            timeout_s=float(os.environ.get("TOPOLOGY_TIMEOUT_S", "3.0")),
        )

    def _fetch(self) -> dict[str, Any]:
        response = requests.get(
            f"{self._base_url}/api/v1/config/adapters",
            headers=self._headers,
            timeout=self._timeout_s,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        payload = body.get("adapters")
        if not isinstance(payload, dict):
            raise TypeError(f"неожиданный payload топологии: {list(body)}")
        return body

    def adapters_map(self, refresh: bool = False) -> dict[str, str] | None:
        """Карта адаптеров; `None` при недоступной топологии.

        Кэш revision + карты: при неизменном revision отдаётся закэшированное.
        """
        with self._lock:
            if not refresh and self._adapters is not None:
                return self._adapters
            try:
                body = self._fetch()
            except (requests.RequestException, ValueError, KeyError, TypeError):
                return None
            self._revision = body.get("revision")
            self._adapters = {str(k): str(v) for k, v in body["adapters"].items()}
            return self._adapters

    def revision(self, refresh: bool = False) -> int | None:
        """Текущая revision (None — топология недоступна с самого старта).

        При сбое опроса после успешного запроса возвращается последняя известная
        revision (заглушка терпима к пропуску поллов)."""
        self.adapters_map(refresh=refresh)
        return self._revision