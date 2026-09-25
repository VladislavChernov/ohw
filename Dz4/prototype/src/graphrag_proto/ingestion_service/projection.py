from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from graphrag_proto.projection_config import load_projection_settings
from graphrag_proto.retrieval.adapters.base import (
    VECTOR_METADATA_BACKFILL_KEYS,
    GraphStoreProvider,
    VectorStoreProvider,
)
from graphrag_proto.security import redact_secrets
from graphrag_proto.sqlite_utils import connect_sqlite

PROJECTION_STATUSES = frozenset({"pending", "ready", "degraded", "stale", "failed"})
PROJECTION_ALGORITHM_VERSION = "projection-v1"
VECTOR_METADATA_KEYS = VECTOR_METADATA_BACKFILL_KEYS
_AUDIT_CHUNK_SAMPLE = 20
_EMBEDDED_URL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>()\[\]]+")
_AUDIT_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "credential",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
    }
)


class ProjectionContractError(ValueError):
    pass
_ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"pending"}),
    "pending": frozenset({"pending", "ready", "degraded", "failed", "stale"}),
    "ready": frozenset({"pending", "stale", "degraded", "failed", "ready"}),
    "degraded": frozenset({"pending", "ready", "degraded", "failed", "stale"}),
    "stale": frozenset({"pending", "ready", "degraded", "stale", "failed"}),
    "failed": frozenset({"pending", "degraded", "failed", "stale"}),
}


def projection_transition_allowed(previous: ProjectionState | None, next_status: str) -> bool:
    previous_status = previous.status if previous is not None else None
    return next_status in _ALLOWED_TRANSITIONS.get(previous_status, frozenset())


class ProjectionLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectionState:
    domain: str
    data_revision: str
    projection_revision: str
    config_fingerprint: str
    status: str
    job_id: str | None = None
    input_source_count: int = 0
    processed_source_count: int = 0
    skipped_source_count: int = 0
    failed_source_count: int = 0
    started_at: str = ""
    finished_at: str | None = None
    last_error: str | None = None
    lease_until: float | None = None

    def __post_init__(self) -> None:
        if not self.domain:
            raise ValueError("projection domain обязателен")
        if self.status not in PROJECTION_STATUSES:
            raise ValueError(f"неизвестный projection status: {self.status!r}")

    @property
    def job_key(self) -> str:
        return f"{self.domain}|{self.data_revision}|{self.projection_revision}|{self.config_fingerprint}"

    def is_ready(
        self,
        data_revision: str | None = None,
        config_fingerprint: str | None = None,
    ) -> bool:
        return (
            self.status == "ready"
            and (data_revision is None or self.data_revision == data_revision)
            and (config_fingerprint is None or self.config_fingerprint == config_fingerprint)
        )


class ProjectionStateStore(ABC):
    @abstractmethod
    def get(self, domain: str) -> ProjectionState | None:
        raise NotImplementedError

    @abstractmethod
    def put(self, state: ProjectionState) -> None:
        raise NotImplementedError

    @abstractmethod
    def compare_and_set(
        self,
        domain: str,
        expected_projection_revision: str | None,
        state: ProjectionState,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def compare_and_set_owned(
        self,
        domain: str,
        expected_job_id: str,
        state: ProjectionState,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def claim(self, state: ProjectionState, lease_seconds: float, now: float | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def renew(
        self,
        domain: str,
        expected_job_id: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> bool:
        raise NotImplementedError


class InMemoryProjectionStateStore(ProjectionStateStore):
    def __init__(self) -> None:
        self._states: dict[str, ProjectionState] = {}
        self._lock = threading.RLock()

    def get(self, domain: str) -> ProjectionState | None:
        with self._lock:
            return self._states.get(domain)

    def put(self, state: ProjectionState) -> None:
        with self._lock:
            self._states[state.domain] = state

    def compare_and_set(
        self,
        domain: str,
        expected_projection_revision: str | None,
        state: ProjectionState,
    ) -> bool:
        with self._lock:
            current = self._states.get(domain)
            if expected_projection_revision is None:
                if current is not None:
                    return False
            elif current is None or current.projection_revision != expected_projection_revision:
                return False
            self._states[domain] = state
            return True

    def compare_and_set_owned(
        self,
        domain: str,
        expected_job_id: str,
        state: ProjectionState,
    ) -> bool:
        with self._lock:
            current = self._states.get(domain)
            if current is None or current.job_id != expected_job_id:
                return False
            if current.lease_until is not None and current.lease_until <= time.time():
                return False
            self._states[domain] = state
            return True

    def claim(self, state: ProjectionState, lease_seconds: float, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else now
        with self._lock:
            current = self._states.get(state.domain)
            if (
                current is not None
                and current.lease_until is not None
                and current.lease_until > timestamp
                and current.job_id != state.job_id
            ):
                return False
            self._states[state.domain] = replace(
                state,
                status="pending",
                lease_until=timestamp + max(0.0, lease_seconds),
            )
            return True

    def renew(
        self,
        domain: str,
        expected_job_id: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self._lock:
            current = self._states.get(domain)
            if current is None or current.job_id != expected_job_id:
                return False
            if current.lease_until is None or current.lease_until <= timestamp:
                return False
            self._states[domain] = replace(
                current,
                lease_until=timestamp + max(0.0, lease_seconds),
            )
            return True


class SQLiteProjectionStateStore(ProjectionStateStore):
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = connect_sqlite(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS projection_states ("
            "domain TEXT PRIMARY KEY, "
            "data_revision TEXT NOT NULL, "
            "projection_revision TEXT NOT NULL, "
            "config_fingerprint TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "job_id TEXT, "
            "input_source_count INTEGER NOT NULL, "
            "processed_source_count INTEGER NOT NULL, "
            "skipped_source_count INTEGER NOT NULL, "
            "failed_source_count INTEGER NOT NULL, "
            "started_at TEXT NOT NULL, "
            "finished_at TEXT, "
            "last_error TEXT, "
            "lease_until REAL"
            ")"
        )
        columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(projection_states)").fetchall()
        }
        migrations = {
            "job_id": "TEXT",
            "input_source_count": "INTEGER NOT NULL DEFAULT 0",
            "processed_source_count": "INTEGER NOT NULL DEFAULT 0",
            "skipped_source_count": "INTEGER NOT NULL DEFAULT 0",
            "failed_source_count": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "TEXT NOT NULL DEFAULT ''",
            "finished_at": "TEXT",
            "last_error": "TEXT",
            "lease_until": "REAL",
        }
        for column, definition in migrations.items():
            if column not in columns:
                self._conn.execute(
                    f"ALTER TABLE projection_states ADD COLUMN {column} {definition}"
                )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get(self, domain: str) -> ProjectionState | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT domain, data_revision, projection_revision, config_fingerprint, status, "
                "job_id, input_source_count, processed_source_count, skipped_source_count, "
                "failed_source_count, started_at, finished_at, last_error, lease_until "
                "FROM projection_states WHERE domain = ?",
                (domain,),
            ).fetchone()
        return self._state_from_row(row) if row else None

    def put(self, state: ProjectionState) -> None:
        with self._lock:
            self._write_state(state)
            self._conn.commit()

    def compare_and_set(
        self,
        domain: str,
        expected_projection_revision: str | None,
        state: ProjectionState,
    ) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT projection_revision FROM projection_states WHERE domain = ?",
                    (domain,),
                ).fetchone()
                current_revision = row[0] if row else None
                if expected_projection_revision is None:
                    if current_revision is not None:
                        self._conn.rollback()
                        return False
                elif current_revision != expected_projection_revision:
                    self._conn.rollback()
                    return False
                self._write_state(state)
                self._conn.commit()
                return True
            except BaseException:
                self._conn.rollback()
                raise

    def compare_and_set_owned(
        self,
        domain: str,
        expected_job_id: str,
        state: ProjectionState,
    ) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT job_id, lease_until FROM projection_states WHERE domain = ?",
                    (domain,),
                ).fetchone()
                if row is None or row[0] != expected_job_id:
                    self._conn.rollback()
                    return False
                if row[1] is not None and float(row[1]) <= time.time():
                    self._conn.rollback()
                    return False
                self._write_state(state)
                self._conn.commit()
                return True
            except BaseException:
                self._conn.rollback()
                raise

    def claim(self, state: ProjectionState, lease_seconds: float, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else now
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT job_id, lease_until FROM projection_states WHERE domain = ?",
                    (state.domain,),
                ).fetchone()
                if (
                    row
                    and row[1] is not None
                    and float(row[1]) > timestamp
                    and row[0] != state.job_id
                ):
                    self._conn.rollback()
                    return False
                self._write_state(
                    replace(
                        state,
                        status="pending",
                        lease_until=timestamp + max(0.0, lease_seconds),
                    )
                )
                self._conn.commit()
                return True
            except BaseException:
                self._conn.rollback()
                raise

    def renew(
        self,
        domain: str,
        expected_job_id: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT job_id, lease_until FROM projection_states WHERE domain = ?",
                    (domain,),
                ).fetchone()
                if row is None or row[0] != expected_job_id:
                    self._conn.rollback()
                    return False
                if row[1] is None or float(row[1]) <= timestamp:
                    self._conn.rollback()
                    return False
                current = self._state_from_row(
                    self._conn.execute(
                        "SELECT domain, data_revision, projection_revision, config_fingerprint, "
                        "status, job_id, input_source_count, processed_source_count, "
                        "skipped_source_count, failed_source_count, started_at, finished_at, "
                        "last_error, lease_until FROM projection_states WHERE domain = ?",
                        (domain,),
                    ).fetchone()
                )
                self._write_state(
                    replace(current, lease_until=timestamp + max(0.0, lease_seconds))
                )
                self._conn.commit()
                return True
            except BaseException:
                self._conn.rollback()
                raise

    @staticmethod
    def _state_from_row(row: tuple[Any, ...]) -> ProjectionState:
        return ProjectionState(
            domain=str(row[0]),
            data_revision=str(row[1]),
            projection_revision=str(row[2]),
            config_fingerprint=str(row[3]),
            status=str(row[4]),
            job_id=str(row[5]) if row[5] is not None else None,
            input_source_count=int(row[6]),
            processed_source_count=int(row[7]),
            skipped_source_count=int(row[8]),
            failed_source_count=int(row[9]),
            started_at=str(row[10]),
            finished_at=str(row[11]) if row[11] is not None else None,
            last_error=str(row[12]) if row[12] is not None else None,
            lease_until=float(row[13]) if row[13] is not None else None,
        )

    def _write_state(self, state: ProjectionState) -> None:
        self._conn.execute(
            "INSERT INTO projection_states ("
            "domain, data_revision, projection_revision, config_fingerprint, status, job_id, "
            "input_source_count, processed_source_count, skipped_source_count, failed_source_count, "
            "started_at, finished_at, last_error, lease_until"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET "
            "data_revision=excluded.data_revision, "
            "projection_revision=excluded.projection_revision, "
            "config_fingerprint=excluded.config_fingerprint, "
            "status=excluded.status, job_id=excluded.job_id, "
            "input_source_count=excluded.input_source_count, "
            "processed_source_count=excluded.processed_source_count, "
            "skipped_source_count=excluded.skipped_source_count, "
            "failed_source_count=excluded.failed_source_count, "
            "started_at=excluded.started_at, finished_at=excluded.finished_at, "
            "last_error=excluded.last_error, lease_until=excluded.lease_until",
            (
                state.domain,
                state.data_revision,
                state.projection_revision,
                state.config_fingerprint,
                state.status,
                state.job_id,
                state.input_source_count,
                state.processed_source_count,
                state.skipped_source_count,
                state.failed_source_count,
                state.started_at,
                state.finished_at,
                state.last_error,
                state.lease_until,
            ),
        )


@dataclass
class ProjectionUnit:
    source_url: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    vector_metadata: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ProjectionMetrics:
    started_total: int = 0
    ready_total: int = 0
    degraded_total: int = 0
    failed_total: int = 0
    stale_total: int = 0
    job_total: int = 0
    job_duration_s: float | None = None
    sources_processed: int = 0
    sources_failed: int = 0
    state_total: int = 0
    fallback_total: int = 0
    fallback_reasons: dict[str, int] = field(default_factory=dict)
    last_duration_s: float | None = None

    @property
    def projection_job_total(self) -> int:
        return self.job_total

    @property
    def projection_job_duration(self) -> float | None:
        return self.job_duration_s

    @property
    def projection_sources_processed(self) -> int:
        return self.sources_processed

    @property
    def projection_sources_failed(self) -> int:
        return self.sources_failed

    @property
    def projection_state_total(self) -> int:
        return self.state_total

    @property
    def projection_fallback_total(self) -> int:
        return self.fallback_total

    @property
    def projection_stale_total(self) -> int:
        return self.stale_total

    def snapshot(self) -> dict[str, Any]:
        return {
            "projection_job_total": self.job_total,
            "projection_job_duration_s": self.job_duration_s,
            "projection_sources_processed": self.sources_processed,
            "projection_sources_failed": self.sources_failed,
            "projection_state_total": self.state_total,
            "projection_fallback_total": self.fallback_total,
            "projection_stale_total": self.stale_total,
            "fallback_reasons": dict(self.fallback_reasons),
        }

    def observe(
        self,
        status: str,
        duration_s: float | None = None,
        *,
        processed_source_count: int = 0,
        failed_source_count: int = 0,
    ) -> None:
        if duration_s is not None:
            self.last_duration_s = round(duration_s, 6)
            self.job_duration_s = self.last_duration_s
        self.state_total += 1
        if status == "pending":
            self.started_total += 1
        elif status == "ready":
            self.ready_total += 1
            self.job_total += 1
            self.sources_processed += processed_source_count
        elif status == "degraded":
            self.degraded_total += 1
            self.job_total += 1
            self.sources_processed += processed_source_count
            self.sources_failed += failed_source_count
        elif status == "failed":
            self.failed_total += 1
            self.job_total += 1
            self.sources_processed += processed_source_count
            self.sources_failed += failed_source_count
        elif status == "stale":
            self.stale_total += 1

    def observe_fallback(self, reason: str) -> None:
        self.fallback_total += 1
        self.fallback_reasons[reason] = self.fallback_reasons.get(reason, 0) + 1


class JsonlProjectionSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __call__(self, domain: str, data_revision: str) -> list[ProjectionUnit]:
        units: list[ProjectionUnit] = []
        for line_number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProjectionContractError(f"line {line_number}: invalid JSON") from exc
            if not isinstance(payload, dict):
                raise ProjectionContractError(f"line {line_number}: expected object")
            payload_domain = str(payload.get("domain") or domain)
            if payload_domain != domain:
                continue
            payload_revision = payload.get("data_revision")
            if payload_revision is not None and str(payload_revision) != data_revision:
                raise ProjectionContractError(
                    f"line {line_number}: data_revision does not match requested revision"
                )
            source_url = str(payload.get("source_url") or "")
            if not source_url:
                raise ProjectionContractError(f"line {line_number}: source_url is required")
            units.append(
                ProjectionUnit(
                    source_url=source_url,
                    nodes=list(payload.get("nodes") or []),
                    edges=list(payload.get("edges") or []),
                    vector_metadata=list(payload.get("vector_metadata") or []),
                )
            )
        return units


ProjectionSourceProvider = Callable[[str, str], list[ProjectionUnit]]


def _source_node_id(domain: str, source_url: str) -> str:
    return f"src:{domain}:{source_url}"


def projection_revision_for(data_revision: str, config_fingerprint: str) -> str:
    payload = json.dumps(
        {
            "algorithm": PROJECTION_ALGORITHM_VERSION,
            "data_revision": data_revision,
            "config_fingerprint": config_fingerprint,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class OfflineProjectionJob:
    def __init__(
        self,
        state_store: ProjectionStateStore,
        graph_store: GraphStoreProvider,
        vector_store: VectorStoreProvider,
        source_provider: ProjectionSourceProvider,
        lease_seconds: float | None = None,
        metrics: ProjectionMetrics | None = None,
        registry: Any | None = None,
    ) -> None:
        self._state_store = state_store
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._source_provider = source_provider
        self._registry = registry
        if lease_seconds is None:
            self._lease_seconds = float(load_projection_settings().lease_seconds)
        else:
            self._lease_seconds = max(0.0, float(lease_seconds))
        self.metrics = metrics or ProjectionMetrics()
        self.audit_log: list[dict[str, Any]] = []

    def _audit(
        self,
        state: ProjectionState,
        event: str,
        duration_s: float | None = None,
    ) -> None:
        self.metrics.observe(
            state.status,
            duration_s,
            processed_source_count=state.processed_source_count,
            failed_source_count=state.failed_source_count,
        )
        self.audit_log.append(
            {
                "event": event,
                "domain": state.domain,
                "job_id": state.job_id,
                "data_revision": state.data_revision,
                "projection_revision": state.projection_revision,
                "config_fingerprint": state.config_fingerprint,
                "status": state.status,
                "input_source_count": state.input_source_count,
                "processed_source_count": state.processed_source_count,
                "failed_source_count": state.failed_source_count,
                "last_error": state.last_error,
                "duration_s": duration_s,
                "ts": _now_iso(),
            }
        )

    def _renew_lease(self, domain: str, job_id: str) -> bool:
        try:
            return self._state_store.renew(domain, job_id, self._lease_seconds)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _validate_unit(domain: str, unit: ProjectionUnit) -> None:
        if not unit.source_url:
            raise ProjectionContractError("ProjectionUnit.source_url обязателен")
        nodes_by_id: dict[str, dict[str, Any]] = {}
        for node in unit.nodes:
            if not isinstance(node, dict):
                raise ProjectionContractError("ProjectionUnit.nodes должен содержать mappings")
            node_id = str(node.get("node_id") or "")
            if not node_id or node_id in nodes_by_id:
                raise ProjectionContractError("node_id должен быть уникальным и непустым")
            properties = node.get("properties") or {}
            if not isinstance(properties, dict):
                raise ProjectionContractError(f"node properties должны быть mapping: {node_id}")
            if properties.get("domain") is not None and str(properties["domain"]) != domain:
                raise ProjectionContractError(f"node {node_id} принадлежит другому domain")
            nodes_by_id[node_id] = node
        source_nodes = [
            node
            for node in unit.nodes
            if "Source" in list(node.get("labels") or [])
            and str((node.get("properties") or {}).get("source_url") or "") == unit.source_url
            and str((node.get("properties") or {}).get("domain") or "") == domain
        ]
        if not source_nodes:
            raise ProjectionContractError("ProjectionUnit должен содержать Source anchor")
        expected_source_id = _source_node_id(domain, unit.source_url)
        if any(str(node.get("node_id") or "") != expected_source_id for node in source_nodes):
            raise ProjectionContractError("Source anchor должен использовать детерминированный node_id")
        chunk_nodes: dict[str, dict[str, Any]] = {}
        for node in unit.nodes:
            if "Chunk" not in list(node.get("labels") or []):
                continue
            properties = node.get("properties") or {}
            chunk_id = str(properties.get("chunk_id") or "")
            if not chunk_id or chunk_id in chunk_nodes:
                raise ProjectionContractError("Chunk anchor должен иметь уникальный chunk_id")
            if properties.get("source_url") is not None and str(properties["source_url"]) != unit.source_url:
                raise ProjectionContractError(f"Chunk anchor {chunk_id} принадлежит другому source")
            if properties.get("domain") is not None and str(properties["domain"]) != domain:
                raise ProjectionContractError(f"Chunk anchor {chunk_id} принадлежит другому domain")
            chunk_nodes[chunk_id] = node
        for edge in unit.edges:
            if not isinstance(edge, dict):
                raise ProjectionContractError("ProjectionUnit.edges должен содержать mappings")
            from_id = str(edge.get("from_id") or "")
            to_id = str(edge.get("to_id") or "")
            edge_type = str(edge.get("type") or "")
            if not from_id or not to_id or not edge_type:
                raise ProjectionContractError("edge требует from_id, to_id и type")
            properties = edge.get("properties") or {}
            if not isinstance(properties, dict):
                raise ProjectionContractError("edge properties должны быть mapping")
            if properties.get("domain") is not None and str(properties["domain"]) != domain:
                raise ProjectionContractError("edge принадлежит другому domain")
            if properties.get("source_ids") is not None and unit.source_url not in list(properties["source_ids"]):
                raise ProjectionContractError("edge provenance не содержит source_url")
        for update in unit.vector_metadata:
            if not isinstance(update, dict):
                raise ProjectionContractError("vector metadata должен содержать mappings")
            metadata = dict(update.get("metadata") or {})
            forbidden = set(metadata) - VECTOR_METADATA_KEYS
            if forbidden:
                raise ProjectionContractError(
                    f"metadata backfill содержит запрещённые поля: {sorted(forbidden)}"
                )
            chunk_id = str(update.get("chunk_id") or "")
            if not chunk_id or chunk_id not in chunk_nodes:
                raise ProjectionContractError("vector metadata ссылается на chunk без Chunk anchor")
            context_ids = {
                str(value)
                for key in ("context_ids", "tag_ids")
                for value in (metadata.get(key) or [])
                if value
            }
            for context_id in context_ids:
                context = nodes_by_id.get(context_id)
                if context is None:
                    raise ProjectionContractError(
                        f"context metadata ссылается на отсутствующий anchor: {context_id}"
                    )
                properties = context.get("properties") or {}
                if properties.get("domain") is not None and str(properties["domain"]) != domain:
                    raise ProjectionContractError(f"context anchor {context_id} принадлежит другому domain")
                source_ids = properties.get("source_ids")
                chunk_ids = properties.get("chunk_ids")
                if isinstance(source_ids, list) and source_ids and unit.source_url not in source_ids:
                    raise ProjectionContractError(f"context anchor {context_id} не содержит source provenance")
                if isinstance(chunk_ids, list) and chunk_ids and chunk_id not in chunk_ids:
                    raise ProjectionContractError(f"context anchor {context_id} не содержит chunk provenance")

    @staticmethod
    def _verify_unit(
        graph_store: GraphStoreProvider,
        vector_store: VectorStoreProvider,
        domain: str,
        unit: ProjectionUnit,
        projection_revision: str,
    ) -> None:
        for node in unit.nodes:
            node_id = str(node.get("node_id") or "")
            if not node_id or graph_store.get_node(node_id) is None:
                raise ProjectionContractError(f"graph anchor не найден после upsert: {node_id}")
        for edge in unit.edges:
            from_id = str(edge.get("from_id") or "")
            to_id = str(edge.get("to_id") or "")
            edge_type = str(edge.get("type") or "")
            if graph_store.get_node(from_id) is None or graph_store.get_node(to_id) is None:
                raise ProjectionContractError("edge endpoint не найден после upsert")
            if not graph_store.verify_edge(from_id, to_id, edge_type):
                raise ProjectionContractError(
                    f"graph edge не найден после upsert: {from_id}->{to_id}:{edge_type}"
                )
        for update in unit.vector_metadata:
            chunk_id = str(update.get("chunk_id") or "")
            metadata = vector_store.get_vector_metadata(chunk_id)
            if metadata is None:
                raise ProjectionContractError(f"vector record не найден после backfill: {chunk_id}")
            if str(metadata.get("source_url") or "") != unit.source_url:
                raise ProjectionContractError(f"vector record {chunk_id} принадлежит другому source")
            if str(metadata.get("domain") or "") != domain:
                raise ProjectionContractError(f"vector record {chunk_id} принадлежит другому domain")
            if str(metadata.get("projection_revision") or "") != projection_revision:
                raise ProjectionContractError(f"vector record {chunk_id} не подтверждает projection revision")
            expected_context_ids = {
                str(value)
                for key in ("context_ids", "tag_ids")
                for value in ((update.get("metadata") or {}).get(key) or [])
                if value
            }
            actual_context_ids = {
                str(value)
                for key in ("context_ids", "tag_ids")
                for value in (metadata.get(key) or [])
                if value
            }
            if not expected_context_ids.issubset(actual_context_ids):
                raise ProjectionContractError(f"vector record {chunk_id} не содержит все context ids")
            for context_id in expected_context_ids:
                context = graph_store.get_node(context_id)
                if context is None:
                    raise ProjectionContractError(f"graph context anchor не найден: {context_id}")
                source_ids = context.get("source_ids") or []
                chunk_ids = context.get("chunk_ids") or []
                if isinstance(source_ids, list) and source_ids and unit.source_url not in source_ids:
                    raise ProjectionContractError(f"context {context_id} не содержит source provenance")
                if isinstance(chunk_ids, list) and chunk_ids and chunk_id not in chunk_ids:
                    raise ProjectionContractError(f"context {context_id} не содержит chunk provenance")

    def _mark_retry(
        self,
        touched_vectors: dict[str, str],
        domain: str,
        projection_revision: str,
        error: str,
    ) -> None:
        marker = {
            "status": "failed",
            "projection_revision": projection_revision,
            "error": error,
            "ts": _now_iso(),
        }
        for chunk_id, source_url in sorted(touched_vectors.items()):
            with suppress(Exception):
                self._vector_store.update_vector_metadata(
                    [
                        {
                            "chunk_id": chunk_id,
                            "source_url": source_url,
                            "domain": domain,
                            "metadata": {"projection_retry": marker},
                        }
                    ]
                )

    @staticmethod
    def _normalize_unit(unit: ProjectionUnit) -> ProjectionUnit:
        nodes = []
        for node in unit.nodes:
            normalized = dict(node)
            normalized["properties"] = dict(node.get("properties") or {})
            nodes.append(normalized)
        edges = []
        for edge in unit.edges:
            normalized = dict(edge)
            normalized["properties"] = dict(edge.get("properties") or {})
            normalized["type"] = str(normalized.get("type") or "").upper()
            edges.append(normalized)
        return replace(
            unit,
            nodes=nodes,
            edges=edges,
            vector_metadata=[dict(update) for update in unit.vector_metadata],
        )

    def _audit_retention(
        self,
        domain: str,
        job_id: str,
        projection_revision: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None:
        """Пишет retention-событие без credential'ов и без неограниченного списка id.

        ``source_url`` может быть presigned-ссылкой, поэтому query-параметры
        подписи вырезаются, а известные секреты из env заменяются на
        ``[REDACTED]`` (L5-02). ``chunk_ids`` логируются только счётчиком и
        первыми ``_AUDIT_CHUNK_SAMPLE`` значениями.
        """
        sample = list(chunk_ids[:_AUDIT_CHUNK_SAMPLE])
        self.audit_log.append(
            {
                "event": "projection_stale_cleanup",
                "domain": domain,
                "job_id": job_id,
                "projection_revision": projection_revision,
                "source_url": _audit_safe_source_url(source_url),
                "chunk_ids_total": len(chunk_ids),
                "chunk_ids_sample": sample,
                "chunk_ids_truncated": len(chunk_ids) > _AUDIT_CHUNK_SAMPLE,
                "ts": _now_iso(),
            }
        )

    def _lease_lost_state(
        self,
        template: ProjectionState,
        *,
        domain: str,
        projection_revision: str,
        job_id: str,
        input_source_count: int,
        processed_source_count: int,
        skipped_source_count: int,
        touched_vectors: dict[str, str],
    ) -> ProjectionState:
        error = "projection_lease_lost"
        self._mark_retry(touched_vectors, domain, projection_revision, error)
        self.audit_log.append(
            {
                "event": "projection_lease_lost",
                "domain": domain,
                "job_id": job_id,
                "data_revision": template.data_revision,
                "projection_revision": projection_revision,
                "status": "pending",
                "last_error": error,
                "ts": _now_iso(),
            }
        )
        return replace(
            template,
            status="pending",
            job_id=job_id,
            lease_until=None,
            input_source_count=input_source_count,
            processed_source_count=processed_source_count,
            skipped_source_count=skipped_source_count,
            failed_source_count=max(input_source_count - processed_source_count, 0),
            finished_at=_now_iso(),
            last_error=error,
        )

    @contextmanager
    def _lock_source(self, domain: str, source_url: str) -> Iterator[None]:
        """Сериализует retention с ingest, который пере-активирует источник.

        ``DocumentRegistry.source_lock`` — общий RLock с ``CommitStage``, поэтому
        удаление не может пересечься с re-активацией того же source. Без registry
        блокировка вырождается в no-op (до этого удаления не доходят: см.
        ``_validate_stale_sources``).
        """
        if self._registry is None:
            yield
            return
        with self._registry.source_lock(domain, source_url):
            yield

    def _validate_stale_sources(self, domain: str, source_urls: list[str]) -> None:
        if not source_urls:
            return
        if self._registry is None:
            raise ProjectionContractError(
                "stale retention требует DocumentRegistry для проверки active sources"
            )
        for source_url in source_urls:
            if self._registry.latest_active(domain, source_url) is not None:
                raise ProjectionContractError(
                    "stale source всё ещё active: "
                    f"domain={domain!r} source_url={_audit_safe_source_url(source_url)!r}"
                )

    def run(
        self,
        domain: str,
        data_revision: str,
        config_fingerprint: str = "default",
        stale_source_urls: list[str] | None = None,
    ) -> ProjectionState:
        projection_revision = projection_revision_for(data_revision, config_fingerprint)
        current = self._state_store.get(domain)
        if (
            current is not None
            and current.is_ready(data_revision, config_fingerprint)
            and not stale_source_urls
        ):
            return current
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        job_id = f"projection-{uuid.uuid4().hex}"
        pending = ProjectionState(
            domain=domain,
            data_revision=data_revision,
            projection_revision=projection_revision,
            config_fingerprint=config_fingerprint,
            status="pending",
            job_id=job_id,
            started_at=started_at,
        )
        if not self._state_store.claim(pending, self._lease_seconds):
            existing = self._state_store.get(domain)
            if existing is not None and existing.is_ready(data_revision, config_fingerprint):
                return existing
            raise ProjectionLeaseError(f"projection job для domain={domain!r} уже захвачен")
        self._audit(pending, "projection_started")
        processed = 0
        total = 0
        stale_count = 0
        touched_vectors: dict[str, str] = {}
        try:
            raw_units = list(self._source_provider(domain, data_revision))
            units = sorted(
                (self._normalize_unit(unit) for unit in raw_units),
                key=lambda unit: unit.source_url,
            )
            source_urls = [unit.source_url for unit in units]
            if len(source_urls) != len(set(source_urls)):
                raise ProjectionContractError("source_url должен быть уникален в projection units")
            total = len(units)
            for unit in units:
                self._validate_unit(domain, unit)
            stale_urls = list(dict.fromkeys(stale_source_urls or []))
            if set(stale_urls) & set(source_urls):
                raise ProjectionContractError("stale source не может одновременно быть active unit")
            self._validate_stale_sources(domain, stale_urls)
            for source_url in stale_urls:
                with self._lock_source(domain, source_url):
                    if self._registry is not None and (
                        self._registry.latest_active(domain, source_url) is not None
                    ):
                        raise ProjectionContractError(
                            "stale source стал active во время retention: "
                            f"domain={domain!r} "
                            f"source_url={_audit_safe_source_url(source_url)!r}"
                        )
                    source_id = _source_node_id(domain, source_url)
                    chunk_ids = set(self._graph_store.list_chunk_ids_of_source(source_id))
                    chunk_ids.update(self._vector_store.list_chunk_ids_of_source(source_url, domain))
                    ordered_chunk_ids = sorted(chunk_ids)
                    self._graph_store.remove_source_from_entities(
                        domain, source_url, ordered_chunk_ids
                    )
                    for chunk_id in ordered_chunk_ids:
                        self._graph_store.delete_node(chunk_id)
                    self._graph_store.delete_node(source_id)
                    self._vector_store.delete_vectors(ordered_chunk_ids)
                self._audit_retention(
                    domain,
                    job_id,
                    projection_revision,
                    source_url,
                    ordered_chunk_ids,
                )
                stale_count += 1
            if not self._renew_lease(domain, job_id):
                return self._lease_lost_state(
                    pending,
                    domain=domain,
                    projection_revision=projection_revision,
                    job_id=job_id,
                    input_source_count=total,
                    processed_source_count=processed,
                    skipped_source_count=stale_count,
                    touched_vectors=touched_vectors,
                )
            for unit in units:
                if not self._renew_lease(domain, job_id):
                    return self._lease_lost_state(
                        pending,
                        domain=domain,
                        projection_revision=projection_revision,
                        job_id=job_id,
                        input_source_count=total,
                        processed_source_count=processed,
                        skipped_source_count=stale_count,
                        touched_vectors=touched_vectors,
                    )
                if unit.nodes:
                    self._graph_store.upsert_nodes(unit.nodes)
                if unit.edges:
                    self._graph_store.upsert_edges(unit.edges)
                updates = []
                for update in unit.vector_metadata:
                    chunk_id = str(update.get("chunk_id") or "")
                    if not chunk_id:
                        raise ProjectionContractError("vector metadata без chunk_id")
                    metadata = dict(update.get("metadata") or {})
                    metadata["projection_revision"] = projection_revision
                    metadata["projection_retry"] = {
                        "status": "ready",
                        "projection_revision": projection_revision,
                        "error": None,
                        "ts": _now_iso(),
                    }
                    updates.append(
                        {
                            "chunk_id": chunk_id,
                            "source_url": unit.source_url,
                            "domain": domain,
                            "replace": True,
                            "metadata": metadata,
                        }
                    )
                    touched_vectors[chunk_id] = unit.source_url
                if updates:
                    updated = self._vector_store.update_vector_metadata(updates)
                    if updated != len(updates):
                        raise ProjectionContractError("vector metadata backfill обновил не все records")
                self._verify_unit(self._graph_store, self._vector_store, domain, unit, projection_revision)
                processed += 1
                if not self._renew_lease(domain, job_id):
                    return self._lease_lost_state(
                        pending,
                        domain=domain,
                        projection_revision=projection_revision,
                        job_id=job_id,
                        input_source_count=total,
                        processed_source_count=processed,
                        skipped_source_count=stale_count,
                        touched_vectors=touched_vectors,
                    )
        except Exception as exc:  # noqa: BLE001
            error_text = _safe_error(exc)
            self._mark_retry(
                touched_vectors,
                domain,
                projection_revision,
                error_text,
            )
            failed = ProjectionState(
                domain=domain,
                data_revision=data_revision,
                projection_revision=projection_revision,
                config_fingerprint=config_fingerprint,
                status="degraded" if processed else "failed",
                job_id=job_id,
                input_source_count=total,
                processed_source_count=processed,
                skipped_source_count=stale_count,
                failed_source_count=max(total - processed, 0),
                started_at=started_at,
                finished_at=_now_iso(),
                last_error=error_text,
            )
            previous = self._state_store.get(domain)
            if not projection_transition_allowed(previous, failed.status):
                if previous is not None and previous.job_id == job_id:
                    return previous
                return self._lease_lost_state(
                    failed,
                    domain=domain,
                    projection_revision=projection_revision,
                    job_id=job_id,
                    input_source_count=total,
                    processed_source_count=processed,
                    skipped_source_count=stale_count,
                    touched_vectors=touched_vectors,
                )
            if not self._state_store.compare_and_set_owned(domain, job_id, failed):
                return self._lease_lost_state(
                    failed,
                    domain=domain,
                    projection_revision=projection_revision,
                    job_id=job_id,
                    input_source_count=total,
                    processed_source_count=processed,
                    skipped_source_count=stale_count,
                    touched_vectors=touched_vectors,
                )
            self._audit(
                failed,
                "projection_failed",
                time.monotonic() - started_monotonic,
            )
            return failed
        ready = ProjectionState(
            domain=domain,
            data_revision=data_revision,
            projection_revision=projection_revision,
            config_fingerprint=config_fingerprint,
            status="ready",
            job_id=job_id,
            input_source_count=total,
            processed_source_count=processed,
            skipped_source_count=stale_count,
            failed_source_count=0,
            started_at=started_at,
            finished_at=_now_iso(),
        )
        previous = self._state_store.get(domain)
        if not projection_transition_allowed(previous, ready.status):
            if previous is not None and previous.job_id == job_id:
                return previous
            return self._lease_lost_state(
                ready,
                domain=domain,
                projection_revision=projection_revision,
                job_id=job_id,
                input_source_count=total,
                processed_source_count=processed,
                skipped_source_count=stale_count,
                touched_vectors=touched_vectors,
            )
        if not self._state_store.compare_and_set_owned(domain, job_id, ready):
            return self._lease_lost_state(
                ready,
                domain=domain,
                projection_revision=projection_revision,
                job_id=job_id,
                input_source_count=total,
                processed_source_count=processed,
                skipped_source_count=stale_count,
                touched_vectors=touched_vectors,
            )
        self._audit(
            ready,
            "projection_ready",
            time.monotonic() - started_monotonic,
        )
        return ready


def main() -> None:
    from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
    from graphrag_proto.retrieval.adapters.factory import build_graph_store, build_vector_store
    from graphrag_proto.security import install_redaction

    install_redaction()

    parser = argparse.ArgumentParser(description="Offline graph projection rebuild")
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--data-revision", required=True)
    parser.add_argument("--config-fingerprint", default="default")
    parser.add_argument("--stale-source-url", action="append", default=[])
    parser.add_argument("--state-db", type=Path)
    args = parser.parse_args()
    ingestion_db = os.environ.get("INGESTION_DB_PATH")
    if not ingestion_db or not Path(ingestion_db).exists():
        # Молчаливый пустой registry допустил бы удаление живого источника,
        # поэтому оператор обязан передать путь к существующей базе ingestion.
        # Проверка идёт до создания state DB: guard не должен оставлять файловых
        # следов при отказе.
        print(
            json.dumps(
                {
                    "state": {
                        "domain": args.domain,
                        "status": "failed",
                        "data_revision": args.data_revision,
                        "config_fingerprint": args.config_fingerprint,
                        "last_error": "ingestion_registry_unavailable: задайте "
                        "INGESTION_DB_PATH на существующую базу ingestion",
                    },
                    "metrics": {},
                    "audit": [],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(2)
    registry = DocumentRegistry(Path(ingestion_db))
    registry_revision = registry.data_revision(args.domain)
    # ``None`` — в домене нет активных документов (полная очистка домена):
    # сверять не с чем, job работает с переданной ревизией.
    if registry_revision is not None and registry_revision != args.data_revision:
        print(
            json.dumps(
                {
                    "state": {
                        "domain": args.domain,
                        "status": "failed",
                        "data_revision": args.data_revision,
                        "config_fingerprint": args.config_fingerprint,
                        "last_error": "data_revision_mismatch: registry="
                        f"{registry_revision!r} requested={args.data_revision!r}",
                    },
                    "metrics": {},
                    "audit": [],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise SystemExit(2)
    state_store = build_projection_state_store(
        str(args.state_db) if args.state_db is not None else None
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=build_graph_store(),
        vector_store=build_vector_store(),
        source_provider=JsonlProjectionSource(args.units),
        registry=registry,
    )
    result = job.run(
        args.domain,
        args.data_revision,
        args.config_fingerprint,
        stale_source_urls=args.stale_source_url,
    )
    print(
        json.dumps(
            {
                "state": asdict(result),
                "metrics": job.metrics.snapshot(),
                "audit": job.audit_log,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if (
        result.status != "ready"
        or result.data_revision != args.data_revision
        or result.config_fingerprint != args.config_fingerprint
    ):
        raise SystemExit(1)


def build_projection_state_store(path: str | Path | None = None) -> SQLiteProjectionStateStore:
    configured = path or os.environ.get("PROJECTION_STATE_DB_PATH", "runtime/projection.db")
    return SQLiteProjectionStateStore(Path(configured))


def try_build_projection_state_store(
    path: str | Path | None = None,
) -> SQLiteProjectionStateStore | None:
    try:
        return build_projection_state_store(path)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _safe_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    # Defence in depth: presigned-URL может не содержать ни одного известного
    # маркера, но query-строка утекла бы в last_error/audit/state DB
    # (L5-02, spec «secrets не логируются»). Все URL-подстроки чистим, даже если
    # они встроены в составное сообщение.
    message = _EMBEDDED_URL_PATTERN.sub(lambda match: _audit_safe_source_url(match.group(0)), message)
    lowered = message.lower()
    if any(
        marker in lowered
        for marker in (
            "password",
            "secret",
            "token",
            "api_key",
            "authorization",
            "unauthorized",
            "credential",
            "bearer",
            "signature",
            "sig=",
            "accesskeyid",
            "access_key",
            "accessid",
            "presigned",
        )
    ):
        return type(exc).__name__
    return message[:512] or type(exc).__name__


def _audit_safe_source_url(source_url: str) -> str:
    """Режет credential-параметры из URL перед записью в audit log.

    Presigned-ссылки (S3/R2/GCS) несут подпись в query-строке, поэтому такие
    параметры удаляются целиком; остальные значения дополнительно проходят
    через :func:`redact_secrets`.
    """
    try:
        parts = urlsplit(source_url)
    except ValueError:
        return redact_secrets(source_url)
    if not parts.scheme or not parts.query:
        # Нет схемы или query — переписывать нечего (кроме известных секретов
        # из env, которые всё равно проходят через redact_secrets).
        return redact_secrets(source_url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _AUDIT_SENSITIVE_QUERY_KEYS
    ]
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    return redact_secrets(cleaned)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
