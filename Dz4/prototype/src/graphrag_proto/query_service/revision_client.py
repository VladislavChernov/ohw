"""RevisionClient — HTTP-клиент к ревизии данных домена (ADR-026, Веха 4-хвост).

Контракт: `GET {INGESTION_URL}/api/v1/ingestion/revision?domain=<domain>` →
`{"revision": <hex|null>, "updated_at": <iso|null>}` (X-API-Key, L5-01).

Fail-open: недоступный ingestion — не ошибка; отдаётся последняя известная
ревизия домена (с первого старта — `None`) — ровно как TopologyClient.
Каждый сбой поллера инкрементирует `revision_poll_errors_total` (паттерн
S6/`topology_poll_errors_total` ревью №7) — деградация не молчит.

`REVISION_POLL_INTERVAL_S = 0` → поллер выключен, все ревизии `None`
(M3-поведение, обратная совместимость с TTL-кэшем).
"""

from __future__ import annotations

import os
import re
import threading
import time

import requests

_INGESTION_DEFAULT_URL = "http://ingestion-api:8002"
_REVISION_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class RevisionClient:
    """Кэширующая обёртка над GET /api/v1/ingestion/revision (thread-safe)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "changeme",
        poll_interval_s: float = 5.0,
        timeout_s: float = 3.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}
        self._poll_interval_s = max(0.0, poll_interval_s)
        self._timeout_s = max(0.1, timeout_s)
        self._lock = threading.Lock()
        # domain -> (revision, updated_at, last_poll)
        self._cache: dict[str, tuple[str | None, str | None, float]] = {}
        self.revision_poll_errors_total = 0

    @classmethod
    def from_env(cls) -> RevisionClient | None:
        url = os.environ.get("INGESTION_URL", _INGESTION_DEFAULT_URL)
        if not url.strip():
            return None
        key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
        return cls(
            url,
            api_key=key,
            poll_interval_s=_env_float("REVISION_POLL_INTERVAL_S", 5.0),
            timeout_s=_env_float("REVISION_TIMEOUT_S", 3.0),
        )

    def _fetch(self, domain: str) -> tuple[str | None, str | None] | None:
        response = requests.get(
            f"{self._base_url}/api/v1/ingestion/revision",
            params={"domain": domain},
            headers=self._headers,
            timeout=self._timeout_s,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise TypeError(f"неожиданный ответ revision: {body!r}")
        if "revision" not in body:
            raise KeyError("revision отсутствует в ответе")
        revision = body.get("revision")
        updated_at = body.get("updated_at")
        if revision is not None and (
            not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision)
        ):
            raise ValueError(f"неожиданная revision: {revision!r}")
        if updated_at is not None and not isinstance(updated_at, str):
            raise TypeError(f"неожиданный updated_at: {updated_at!r}")
        return revision, updated_at

    def revision(self, domain: str) -> str | None:
        """Текущая ревизия домена (None — неизвестна/поллер выключен).

        Пер-доменный кэш с окном `poll_interval_s` (не более 1 GET на домен
        в окно). Сбой — последняя известная ревизия; с самого старта — None.
        """
        if self._poll_interval_s <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(domain)
            if cached is not None and now - cached[2] < self._poll_interval_s:
                return cached[0]
        try:
            revision, updated_at = self._fetch(domain) or (None, None)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            with self._lock:
                self.revision_poll_errors_total += 1
                cached = self._cache.get(domain)
            return cached[0] if cached is not None else None
        with self._lock:
            self._cache[domain] = (revision, updated_at, time.monotonic())
        return revision

    def known_revisions(self) -> dict[str, str | None]:
        """Известные ревизии доменов {domain: revision} — видимость для оператора."""
        with self._lock:
            return {domain: cached[0] for domain, cached in self._cache.items()}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default