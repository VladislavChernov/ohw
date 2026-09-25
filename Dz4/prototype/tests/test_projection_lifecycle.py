from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from graphrag_proto.ingestion_service.document import Document
from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    CommitStage,
    PipelineContext,
    soft_delete_source,
)
from graphrag_proto.ingestion_service.projection import (
    InMemoryProjectionStateStore,
    JsonlProjectionSource,
    OfflineProjectionJob,
    ProjectionContractError,
    ProjectionState,
    ProjectionUnit,
    SQLiteProjectionStateStore,
    _safe_error,
    projection_revision_for,
    projection_transition_allowed,
)
from graphrag_proto.ingestion_service.projection import main as projection_main
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.deterministic import (
    DeterministicEmbedder,
    deterministic_embedding,
)
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.pipeline import QueryPipeline
from graphrag_proto.retrieval.profile import DomainProfileLoader
from graphrag_proto.retrieval.semantic_cache import CachedAnswer, InMemorySemanticCache


class _ProfileLoader(DomainProfileLoader):
    def load(self, domain: str | None = None) -> dict[str, Any]:
        return {
            "retrieval": {"graph_search_enabled": True, "graph_boost": 0.1},
            "context_assembly": {"max_tokens": 128},
        }


class _CountingGraphStore(InMemoryGraphStore):
    def __init__(self) -> None:
        super().__init__()
        self.expansion_calls: list[list[str]] = []

    def expand(self, context_ids: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        self.expansion_calls.append(list(context_ids))
        return [
            {
                "node_id": "tag:it:parent",
                "canonical_name": "Parent",
                "path": ["tag:it:seed", "tag:it:parent"],
                "depth": 1,
                "kind": "parent",
                "origin": "system",
                "confidence": 1.0,
                "source_ids": ["src://doc"],
                "chunk_ids": ["chk:seed"],
                "domain": "it",
            }
        ]


def _ingestion_db(tmp_path: Path, name: str = "ingestion.db") -> Path:
    """Существующая база ingestion: ``main()`` отказывается работать без неё."""
    path = tmp_path / name
    DocumentRegistry(path)
    return path


def _state(
    *,
    domain: str = "it",
    status: str = "pending",
    data_revision: str = "data-1",
    projection_revision: str = "projection-1",
) -> ProjectionState:
    return ProjectionState(
        domain=domain,
        data_revision=data_revision,
        projection_revision=projection_revision,
        config_fingerprint="config-1",
        status=status,
        job_id="job-1",
        input_source_count=1,
        processed_source_count=0,
        skipped_source_count=0,
        failed_source_count=0,
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_projection_state_store_claim_and_compare_and_set(tmp_path: Path) -> None:
    store = SQLiteProjectionStateStore(tmp_path / "projection.db")
    pending = _state()

    assert store.compare_and_set("it", None, pending) is True
    assert store.compare_and_set("it", "other", _state(status="ready")) is False
    ready = replace(pending, status="ready", projection_revision="projection-1", processed_source_count=1)
    assert store.compare_and_set("it", pending.projection_revision, ready) is True
    assert store.get("it") == ready
    assert store.get("library") is None


def test_sqlite_state_store_fences_expired_owner(tmp_path: Path) -> None:
    store = SQLiteProjectionStateStore(tmp_path / "projection.db")
    now = time.time()
    expired = replace(_state(status="pending"), job_id="job-1", lease_until=now - 1)
    store.put(expired)
    assert store.compare_and_set_owned("it", "job-1", replace(expired, status="ready")) is False

    replacement = replace(_state(status="pending"), job_id="job-2")
    assert store.claim(replacement, lease_seconds=60, now=now) is True
    assert store.renew("it", "job-2", lease_seconds=60, now=now) is True
    assert store.compare_and_set_owned("it", "job-1", replace(expired, status="ready")) is False
    assert store.compare_and_set_owned(
        "it",
        "job-2",
        replace(replacement, status="ready", lease_until=None),
    ) is True
    store.close()


def test_jsonl_projection_source_loads_domain_units(tmp_path: Path) -> None:
    path = tmp_path / "units.jsonl"
    path.write_text(
        '{"domain":"it","source_url":"src://doc","nodes":[],"edges":[],"vector_metadata":[]}\n',
        encoding="utf-8",
    )
    source = JsonlProjectionSource(path)

    units = source("it", "data-1")

    assert len(units) == 1
    assert units[0].source_url == "src://doc"


def test_jsonl_projection_source_rejects_revision_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "units.jsonl"
    path.write_text(
        '{"domain":"it","data_revision":"data-2","source_url":"src://doc",'
        '"nodes":[],"edges":[],"vector_metadata":[]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ProjectionContractError, match="data_revision"):
        JsonlProjectionSource(path)("it", "data-1")


def test_projection_lease_and_transition_contract() -> None:
    store = InMemoryProjectionStateStore()
    pending = _state()
    assert projection_transition_allowed(None, "pending") is True
    assert projection_transition_allowed(pending, "ready") is True
    assert projection_transition_allowed(replace(pending, status="failed"), "ready") is False
    assert projection_transition_allowed(replace(pending, status="failed"), "stale") is True
    assert (
        projection_transition_allowed(replace(pending, status="degraded"), "degraded") is True
    )
    assert projection_transition_allowed(replace(pending, status="stale"), "stale") is True
    assert store.claim(pending, lease_seconds=10, now=100.0) is True
    assert store.claim(replace(pending, job_id="job-2"), lease_seconds=10, now=105.0) is False
    assert store.claim(replace(pending, job_id="job-2"), lease_seconds=10, now=111.0) is True


def test_commit_stage_records_ready_projection_state(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "documents.db")
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    document = Document(
        source_id="",
        source_url="src://doc",
        domain="it",
        doc_type="txt",
        content_hash="content-1",
    )
    ctx = PipelineContext(
        job_id="job-1",
        domain="it",
        doc_type="txt",
        source_url="src://doc",
        document=document,
        chunks=["seed"],
        chunks_meta=[{"index": 0, "chunk_id": "chk:seed", "embedding": [0.1] * 8}],
    )

    CommitStage(
        registry,
        graph_store=graph,
        vector_store=vector,
        projection_state_store=state_store,
    ).run(ctx)

    state = state_store.get("it")
    assert state is not None
    assert state.status == "ready"
    assert state.data_revision == registry.data_revision("it")


def test_offline_projection_job_backfills_metadata_idempotently() -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {
                    "source_url": "src://doc",
                    "domain": "it",
                    "text": "seed",
                    "context_ids": ["tag:it:old"],
                },
            }
        ]
    )

    def source_provider(domain: str, data_revision: str) -> list[ProjectionUnit]:
        assert domain == "it"
        assert data_revision == "data-1"
        return [
            ProjectionUnit(
                source_url="src://doc",
                nodes=[
                    {
                        "node_id": "src:it:src://doc",
                        "labels": ["Source"],
                        "properties": {
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                    {
                        "node_id": "chk:seed",
                        "labels": ["Chunk"],
                        "properties": {
                            "chunk_id": "chk:seed",
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                    {
                        "node_id": "tag:it:seed",
                        "labels": ["ContextNode"],
                        "properties": {
                            "tag_id": "tag:it:seed",
                            "domain": "it",
                            "canonical_name": "Seed",
                            "origin": "system",
                            "source_ids": ["src://doc"],
                            "chunk_ids": ["chk:seed"],
                        },
                    }
                ],
                edges=[],
                vector_metadata=[
                    {
                        "chunk_id": "chk:seed",
                        "metadata": {
                            "context_ids": ["tag:it:seed"],
                            "tag_ids": ["tag:it:seed"],
                            "projection_revision": "projection-1",
                        },
                    }
                ],
            )
        ]

    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=source_provider,
    )
    first = job.run("it", "data-1")
    second = job.run("it", "data-1")

    assert first.status == "ready"
    assert [event["status"] for event in job.audit_log] == ["pending", "ready"]
    assert job.metrics.ready_total == 1
    assert second.projection_revision == first.projection_revision
    assert second.job_id == first.job_id
    assert graph.get_node("tag:it:seed") is not None
    row = vector.vector_search(deterministic_embedding("seed"), top_k=1, domain="it")[0]
    assert row["context_ids"] == ["tag:it:seed"]
    assert row["tag_ids"] == ["tag:it:seed"]
    assert row["projection_revision"] == first.projection_revision
    assert row["projection_retry"]["status"] == "ready"
    assert row["projection_retry"]["error"] is None


def test_offline_projection_job_records_failure_without_breaking_vector_baseline() -> None:
    class FailingGraph(InMemoryGraphStore):
        def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
            raise RuntimeError("projection failed")

    state_store = InMemoryProjectionStateStore()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {"source_url": "src://doc", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=FailingGraph(),
        vector_store=vector,
        source_provider=lambda _domain, _revision: [
            ProjectionUnit(
                source_url="src://doc",
                nodes=[
                    {
                        "node_id": "src:it:src://doc",
                        "labels": ["Source"],
                        "properties": {
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                    {
                        "node_id": "chk:seed",
                        "labels": ["Chunk"],
                        "properties": {
                            "chunk_id": "chk:seed",
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                    {
                        "node_id": "tag:it:seed",
                        "labels": ["ContextNode"],
                        "properties": {"tag_id": "tag:it:seed", "domain": "it"},
                    }
                ],
            )
        ],
    )

    result = job.run("it", "data-1")

    assert result.status == "failed"
    assert [event["status"] for event in job.audit_log] == ["pending", "failed"]
    assert job.metrics.failed_total == 1
    assert result.last_error == "projection failed"
    assert vector.vector_search(deterministic_embedding("seed"), top_k=1, domain="it")


def test_projection_job_cleans_stale_source_before_backfill(tmp_path: Path) -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    registry = DocumentRegistry(tmp_path / "documents.db")
    source_id = "src:it:src://old"
    graph.upsert_nodes(
        [
            {
                "node_id": source_id,
                "labels": ["Source"],
                "properties": {"source_url": "src://old", "domain": "it"},
            },
            {
                "node_id": "chk:old",
                "labels": ["Chunk"],
                "properties": {"chunk_id": "chk:old", "source_url": "src://old", "domain": "it"},
            },
            {
                "node_id": "tag:it:old",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:old",
                    "domain": "it",
                    "source_ids": ["src://old"],
                    "chunk_ids": ["chk:old"],
                },
            },
        ]
    )
    graph.upsert_edges(
        [
            {
                "from_id": source_id,
                "to_id": "chk:old",
                "type": "CONTAINS",
                "properties": {"source_ids": ["src://old"], "chunk_ids": ["chk:old"]},
            }
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:old",
                "embedding": deterministic_embedding("old"),
                "metadata": {"source_url": "src://old", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
        registry=registry,
    )

    result = job.run("it", "data-1", stale_source_urls=["src://old"])

    assert result.status == "ready"
    assert result.skipped_source_count == 1
    assert graph.get_node("chk:old") is None
    assert vector.get_vector_metadata("chk:old") is None


def test_projection_job_never_returns_foreign_state_after_lease_loss() -> None:
    class StolenLeaseStore(InMemoryProjectionStateStore):
        def renew(
            self,
            domain: str,
            expected_job_id: str,
            lease_seconds: float,
            now: float | None = None,
        ) -> bool:
            self.put(
                ProjectionState(
                    domain=domain,
                    data_revision="other-revision",
                    projection_revision="other-projection",
                    config_fingerprint="other-config",
                    status="ready",
                    job_id="other-job",
                )
            )
            return False

    state_store = StolenLeaseStore()
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        source_provider=lambda _domain, _revision: [],
    )

    result = job.run("it", "data-1")

    assert result.status == "pending"
    assert result.data_revision == "data-1"
    assert result.job_id != "other-job"
    assert state_store.get("it") is not None
    assert state_store.get("it").job_id == "other-job"


def test_projection_job_does_not_overwrite_lost_lease() -> None:
    class FencedStore(InMemoryProjectionStateStore):
        def compare_and_set_owned(
            self,
            domain: str,
            expected_job_id: str,
            state: ProjectionState,
        ) -> bool:
            return False

    state_store = FencedStore()
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        source_provider=lambda _domain, _revision: [],
    )

    result = job.run("it", "data-1")

    assert result.status == "pending"
    assert state_store.get("it") is not None


def test_projection_job_rejects_missing_vector_record() -> None:
    state_store = InMemoryProjectionStateStore()
    vector = InMemoryVectorStore()
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=InMemoryGraphStore(),
        vector_store=vector,
        source_provider=lambda _domain, _revision: [
            ProjectionUnit(
                source_url="src://doc",
                nodes=[
                    {
                        "node_id": "src:it:src://doc",
                        "labels": ["Source"],
                        "properties": {"source_url": "src://doc", "domain": "it"},
                    },
                    {
                        "node_id": "chk:missing",
                        "labels": ["Chunk"],
                        "properties": {"chunk_id": "chk:missing", "domain": "it"},
                    },
                ],
                vector_metadata=[{"chunk_id": "chk:missing", "metadata": {}}],
            )
        ],
    )

    result = job.run("it", "data-1")

    assert result.status == "failed"
    assert "vector record" in (result.last_error or "")


def test_projection_job_rejects_vector_body_metadata() -> None:
    state_store = InMemoryProjectionStateStore()
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        source_provider=lambda _domain, _revision: [
            ProjectionUnit(
                source_url="src://doc",
                nodes=[
                    {
                        "node_id": "src:it:src://doc",
                        "labels": ["Source"],
                        "properties": {"source_url": "src://doc", "domain": "it"},
                    },
                    {
                        "node_id": "chk:x",
                        "labels": ["Chunk"],
                        "properties": {"chunk_id": "chk:x", "domain": "it"},
                    },
                ],
                vector_metadata=[{"chunk_id": "chk:x", "metadata": {"text": "forbidden"}}],
            )
        ],
    )

    result = job.run("it", "data-1")

    assert result.status == "failed"
    assert "запрещённые поля" in (result.last_error or "")


def test_ready_projection_revision_isolates_graph_cache() -> None:
    state_store = InMemoryProjectionStateStore()
    ready_projection = projection_revision_for("data-1", "config-1")
    state_store.put(
        _state(status="ready", data_revision="data-1", projection_revision=ready_projection)
    )
    graph = _CountingGraphStore()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {
                    "source_url": "src://doc",
                    "domain": "it",
                    "text": "seed",
                    "context_ids": ["tag:it:seed"],
                    "projection_revision": ready_projection,
                },
            }
        ]
    )
    cache = InMemorySemanticCache(threshold=0.8, ttl_s=0)
    pipeline = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_ProfileLoader(),
        semantic_cache=cache,
        projection_state_store=state_store,
        projection_config_fingerprint="config-1",
    )

    first = pipeline.run("seed", domain="it", revision="data-1")
    second = pipeline.run("seed", domain="it", revision="data-1")

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["projection_revision"] == ready_projection
    assert graph.expansion_calls == [["tag:it:seed"]]


def test_offline_projection_failure_marks_backfill_for_retry() -> None:
    class MissingContextAfterWrite(InMemoryGraphStore):
        def get_node(self, node_id: str) -> dict[str, Any] | None:
            if node_id == "tag:it:retry":
                return None
            return super().get_node(node_id)

    state_store = InMemoryProjectionStateStore()
    graph = MissingContextAfterWrite()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:retry",
                "embedding": deterministic_embedding("retry"),
                "metadata": {"source_url": "src://doc", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [
            ProjectionUnit(
                source_url="src://doc",
                nodes=[
                    {
                        "node_id": "src:it:src://doc",
                        "labels": ["Source"],
                        "properties": {"source_url": "src://doc", "domain": "it"},
                    },
                    {
                        "node_id": "chk:retry",
                        "labels": ["Chunk"],
                        "properties": {
                            "chunk_id": "chk:retry",
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                    {
                        "node_id": "tag:it:retry",
                        "labels": ["ContextNode"],
                        "properties": {
                            "tag_id": "tag:it:retry",
                            "domain": "it",
                            "source_ids": ["src://doc"],
                            "chunk_ids": ["chk:retry"],
                        },
                    },
                ],
                vector_metadata=[
                    {
                        "chunk_id": "chk:retry",
                        "metadata": {"context_ids": ["tag:it:retry"]},
                    }
                ],
            )
        ],
    )

    result = job.run("it", "data-1")

    assert result.status == "failed"
    metadata = vector.get_vector_metadata("chk:retry")
    assert metadata is not None
    assert metadata["projection_retry"]["status"] == "failed"
    assert vector._vectors["chk:retry"]["embedding"] == deterministic_embedding("retry")


def test_query_pipeline_blocks_graph_until_projection_is_ready() -> None:
    state_store = InMemoryProjectionStateStore()
    ready_projection = projection_revision_for("data-1", "config-1")
    graph = _CountingGraphStore()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {
                    "source_url": "src://doc",
                    "domain": "it",
                    "text": "seed",
                    "context_ids": ["tag:it:seed"],
                    "projection_revision": ready_projection,
                },
            }
        ]
    )
    pipeline = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_ProfileLoader(),
        projection_state_store=state_store,
        projection_config_fingerprint="config-1",
    )

    blocked = pipeline.run("seed", domain="it", revision="data-1", generate=False, trace=True)
    assert blocked["graph_degraded"] is True
    assert blocked["projection_status"] == "missing"
    assert graph.expansion_calls == []

    state_store.put(
        _state(status="ready", data_revision="data-1", projection_revision=ready_projection)
    )
    ready = pipeline.run("seed", domain="it", revision="data-1", generate=False, trace=True)
    assert ready["graph_degraded"] is False
    assert ready["projection_revision"] == ready_projection
    assert graph.expansion_calls == [["tag:it:seed"]]

    state_store.put(_state(status="ready", data_revision="data-0", projection_revision="projection-0"))
    stale = pipeline.run("seed", domain="it", revision="data-1", generate=False, trace=True)
    assert stale["graph_degraded"] is True
    assert stale["projection_status"] == "stale"
    assert graph.expansion_calls == [["tag:it:seed"]]

    unknown = pipeline.run("seed", domain="it", generate=False, trace=True)
    assert unknown["graph_degraded"] is True
    assert unknown["projection_status"] == "revision_unknown"
    assert graph.expansion_calls == [["tag:it:seed"]]


def test_query_pipeline_uses_vector_cache_when_projection_is_missing() -> None:
    state_store = InMemoryProjectionStateStore()
    cache = InMemorySemanticCache(threshold=0.8, ttl_s=0)
    cache.store(
        deterministic_embedding("seed"),
        CachedAnswer(text="vector-only cached", sources=[]),
        domain="it",
        revision="data-1:vector:none",
    )
    graph = _CountingGraphStore()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {
                    "source_url": "src://doc",
                    "domain": "it",
                    "context_ids": ["tag:it:seed"],
                },
            }
        ]
    )
    pipeline = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_ProfileLoader(),
        semantic_cache=cache,
        projection_state_store=state_store,
        projection_config_fingerprint="config-1",
    )

    result = pipeline.run("seed", domain="it", revision="data-1")

    assert result["cache_hit"] is True
    assert result["graph_degraded"] is True
    assert result["projection_status"] == "missing"
    assert graph.expansion_calls == []


def test_offline_projection_preserves_manual_provenance_on_ai_rebuild() -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    graph.upsert_nodes(
        [
            {
                "node_id": "tag:it:manual",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:manual",
                    "domain": "it",
                    "canonical_name": "Manual",
                    "origin": "user",
                    "source_ids": ["src://doc"],
                    "chunk_ids": ["chk:seed"],
                    "properties": {"manual": True},
                },
            },
            {
                "node_id": "tag:it:parent",
                "labels": ["ContextNode"],
                "properties": {"tag_id": "tag:it:parent", "domain": "it", "origin": "user"},
            },
        ]
    )
    graph.upsert_edges(
        [
            {
                "from_id": "tag:it:manual",
                "to_id": "tag:it:parent",
                "type": "RELATED",
                "properties": {
                    "kind": "parent",
                    "origin": "user",
                    "source_ids": ["src://doc"],
                    "chunk_ids": ["chk:seed"],
                    "properties": {"label": "manual"},
                },
            }
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {"source_url": "src://doc", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [
            ProjectionUnit(
                source_url="src://doc",
                nodes=[
                    {
                        "node_id": "src:it:src://doc",
                        "labels": ["Source"],
                        "properties": {
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                    {
                        "node_id": "chk:seed",
                        "labels": ["Chunk"],
                        "properties": {
                            "chunk_id": "chk:seed",
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                    {
                        "node_id": "tag:it:manual",
                        "labels": ["ContextNode"],
                        "properties": {
                            "tag_id": "tag:it:manual",
                            "domain": "it",
                            "canonical_name": "AI",
                            "origin": "ai",
                            "source_ids": ["src://doc"],
                            "chunk_ids": ["chk:seed"],
                            "properties": {"ai": True},
                        },
                    },
                    {
                        "node_id": "tag:it:parent",
                        "labels": ["ContextNode"],
                        "properties": {
                            "tag_id": "tag:it:parent",
                            "domain": "it",
                            "canonical_name": "Parent",
                            "origin": "ai",
                        },
                    },
                ],
                edges=[
                    {
                        "from_id": "tag:it:manual",
                        "to_id": "tag:it:parent",
                        "type": "RELATED",
                        "properties": {
                            "kind": "parent",
                            "origin": "ai",
                            "source_ids": ["src://doc"],
                            "chunk_ids": ["chk:seed"],
                            "properties": {"ai": True},
                        },
                    }
                ],
                vector_metadata=[
                    {
                        "chunk_id": "chk:seed",
                        "metadata": {
                            "context_ids": ["tag:it:manual"],
                            "tag_ids": ["tag:it:manual"],
                        },
                    }
                ],
            )
        ],
    )

    result = job.run("it", "data-1")

    assert result.status == "ready"
    context = graph.get_node("tag:it:manual")
    assert context is not None
    assert context["canonical_name"] == "Manual"
    assert context["origin"] == "user"
    assert context["properties"] == {"manual": True}
    assert context["source_ids"] == ["src://doc"]
    assert context["chunk_ids"] == ["chk:seed"]
    edge = graph._edges[("tag:it:manual", "tag:it:parent", "RELATED")]
    assert edge["origin"] == "user"
    assert edge["properties"] == {"label": "manual"}
    assert edge["source_ids"] == ["src://doc"]
    assert edge["chunk_ids"] == ["chk:seed"]


def test_offline_projection_rejects_context_without_anchor() -> None:
    state_store = InMemoryProjectionStateStore()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {"source_url": "src://doc", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=InMemoryGraphStore(),
        vector_store=vector,
        source_provider=lambda _domain, _revision: [
            ProjectionUnit(
                source_url="src://doc",
                nodes=[
                    {
                        "node_id": "src:it:src://doc",
                        "labels": ["Source"],
                        "properties": {"source_url": "src://doc", "domain": "it"},
                    },
                    {
                        "node_id": "chk:seed",
                        "labels": ["Chunk"],
                        "properties": {
                            "chunk_id": "chk:seed",
                            "source_url": "src://doc",
                            "domain": "it",
                        },
                    },
                ],
                vector_metadata=[
                    {
                        "chunk_id": "chk:seed",
                        "metadata": {"context_ids": ["tag:it:missing"]},
                    }
                ],
            )
        ],
    )

    result = job.run("it", "data-1")

    assert result.status == "failed"
    assert "context" in (result.last_error or "").lower()


def test_offline_projection_stale_cleanup_removes_source_anchor(tmp_path: Path) -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    registry = DocumentRegistry(tmp_path / "documents.db")
    source_id = "src:it:src://old"
    graph.upsert_nodes(
        [
            {
                "node_id": source_id,
                "labels": ["Source"],
                "properties": {"source_url": "src://old", "domain": "it"},
            },
            {
                "node_id": "chk:old",
                "labels": ["Chunk"],
                "properties": {"chunk_id": "chk:old", "source_url": "src://old", "domain": "it"},
            },
            {
                "node_id": "tag:it:parent",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:parent",
                    "domain": "it",
                    "source_ids": ["src://old"],
                    "chunk_ids": ["chk:old"],
                },
            },
        ]
    )
    graph.upsert_edges(
        [
            {
                "from_id": source_id,
                "to_id": "chk:old",
                "type": "CONTAINS",
                "properties": {
                    "domain": "it",
                    "source_ids": ["src://old"],
                    "chunk_ids": ["chk:old"],
                },
            }
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:old",
                "embedding": deterministic_embedding("old"),
                "metadata": {"source_url": "src://old", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
        registry=registry,
    )

    result = job.run("it", "data-1", stale_source_urls=["src://old"])

    assert result.status == "ready"
    assert graph.get_node(source_id) is None
    assert graph._edges == {}


def test_query_pipeline_blocks_ready_state_with_stale_vector_projection_marker() -> None:
    state_store = InMemoryProjectionStateStore()
    state_store.put(
        _state(
            status="ready",
            data_revision="data-1",
            projection_revision=projection_revision_for("data-1", "config-1"),
        )
    )
    graph = _CountingGraphStore()
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:seed",
                "embedding": deterministic_embedding("seed"),
                "metadata": {
                    "source_url": "src://doc",
                    "domain": "it",
                    "context_ids": ["tag:it:seed"],
                    "projection_revision": "old-projection",
                },
            }
        ]
    )
    pipeline = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_ProfileLoader(),
        projection_state_store=state_store,
        projection_config_fingerprint="config-1",
    )

    result = pipeline.run("seed", domain="it", revision="data-1", generate=False, trace=True)

    assert result["graph_degraded"] is True
    assert result["projection_status"] == "projection_metadata_mismatch"
    assert graph.expansion_calls == []


def test_commit_stage_noop_records_pending_projection_state(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "documents.db")
    document = Document(
        source_id="",
        source_url="src://doc",
        domain="it",
        doc_type="txt",
        content_hash="content-1",
    )
    registry.upsert(document)
    state_store = InMemoryProjectionStateStore()
    ctx = PipelineContext(
        job_id="job-1",
        domain="it",
        doc_type="txt",
        source_url="src://doc",
        document=document,
    )
    stage = CommitStage(
        registry,
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        projection_state_store=state_store,
    )

    assert stage.try_noop(ctx) is True

    state = state_store.get("it")
    assert state is not None
    assert state.status == "pending"
    assert state.data_revision == registry.data_revision("it")


def test_soft_delete_marks_projection_stale_and_removes_chunks(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("PROJECTION_CONFIG_FINGERPRINT", "config-1")
    registry = DocumentRegistry(tmp_path / "documents.db")
    document = Document(
        source_id="",
        source_url="src://doc",
        domain="it",
        doc_type="txt",
        content_hash="content-1",
    )
    registry.upsert(document)
    data_revision = registry.data_revision("it")
    assert data_revision is not None
    state_store = InMemoryProjectionStateStore()
    state_store.put(
        _state(
            status="ready",
            data_revision=data_revision,
            projection_revision=projection_revision_for(data_revision, "config-1"),
        )
    )
    graph = InMemoryGraphStore()
    source_id = "src:it:src://doc"
    graph.upsert_nodes(
        [
            {
                "node_id": source_id,
                "labels": ["Source"],
                "properties": {"source_url": "src://doc", "domain": "it"},
            },
            {
                "node_id": "chk:doc",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:doc",
                    "source_url": "src://doc",
                    "domain": "it",
                },
            },
        ]
    )
    graph.upsert_edges(
        [
            {
                "from_id": source_id,
                "to_id": "chk:doc",
                "type": "CONTAINS",
                "properties": {
                    "domain": "it",
                    "source_ids": ["src://doc"],
                    "chunk_ids": ["chk:doc"],
                },
            }
        ]
    )
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:doc",
                "embedding": deterministic_embedding("doc"),
                "metadata": {"source_url": "src://doc", "domain": "it"},
            }
        ]
    )

    assert soft_delete_source(
        registry,
        graph,
        vector,
        "it",
        "src://doc",
        projection_state_store=state_store,
    ) is True

    state = state_store.get("it")
    assert state is not None
    assert state.status == "stale"
    assert graph.get_node(source_id) is not None
    assert graph.get_node("chk:doc") is None
    assert vector.get_vector_metadata("chk:doc") is None


def test_offline_projection_renews_lease_between_source_units() -> None:
    state_store = InMemoryProjectionStateStore()
    vector = InMemoryVectorStore()

    class SlowGraph(InMemoryGraphStore):
        def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
            time.sleep(0.03)
            super().upsert_nodes(nodes)

    def source_provider(_domain: str, _revision: str) -> list[ProjectionUnit]:
        units: list[ProjectionUnit] = []
        for index in range(3):
            source_url = f"src://doc-{index}"
            units.append(
                ProjectionUnit(
                    source_url=source_url,
                    nodes=[
                        {
                            "node_id": f"src:it:{source_url}",
                            "labels": ["Source"],
                            "properties": {"source_url": source_url, "domain": "it"},
                        }
                    ],
                )
            )
        return units

    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=SlowGraph(),
        vector_store=vector,
        source_provider=source_provider,
        lease_seconds=0.05,
    )

    result = job.run("it", "data-1")

    assert result.status == "ready"
    assert [event["event"] for event in job.audit_log] == [
        "projection_started",
        "projection_ready",
    ]


def test_offline_projection_refuses_to_delete_active_source(tmp_path: Path) -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    registry = DocumentRegistry(tmp_path / "documents.db")
    document = Document(
        source_id="",
        source_url="src://active",
        domain="it",
        doc_type="txt",
        content_hash="content-active",
    )
    registry.upsert(document)
    graph.upsert_nodes(
        [
            {
                "node_id": "src:it:src://active",
                "labels": ["Source"],
                "properties": {"source_url": "src://active", "domain": "it"},
            },
            {
                "node_id": "chk:active",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:active",
                    "source_url": "src://active",
                    "domain": "it",
                },
            },
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:active",
                "embedding": deterministic_embedding("active"),
                "metadata": {"source_url": "src://active", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
        registry=registry,
    )

    result = job.run("it", "data-1", stale_source_urls=["src://active"])

    assert result.status == "failed"
    assert "active" in (result.last_error or "")
    assert vector.get_vector_metadata("chk:active") is not None
    assert graph.get_node("chk:active") is not None


def test_offline_projection_job_does_not_touch_other_domain(tmp_path: Path) -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    registry = DocumentRegistry(tmp_path / "documents.db")
    it_source_id = "src:it:src://shared"
    library_source_id = "src:library:src://shared"
    it_state = _state(
        domain="it",
        status="ready",
        data_revision="it-rev",
        projection_revision="it-projection",
    )
    state_store.put(it_state)
    graph.upsert_nodes(
        [
            {
                "node_id": it_source_id,
                "labels": ["Source"],
                "properties": {"source_url": "src://shared", "domain": "it"},
            },
            {
                "node_id": "chk:it",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:it",
                    "source_url": "src://shared",
                    "domain": "it",
                },
            },
            {
                "node_id": library_source_id,
                "labels": ["Source"],
                "properties": {"source_url": "src://shared", "domain": "library"},
            },
            {
                "node_id": "chk:library",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:library",
                    "source_url": "src://shared",
                    "domain": "library",
                },
            },
        ]
    )
    graph.upsert_edges(
        [
            {
                "from_id": it_source_id,
                "to_id": "chk:it",
                "type": "CONTAINS",
                "properties": {
                    "domain": "it",
                    "source_ids": ["src://shared"],
                    "chunk_ids": ["chk:it"],
                },
            },
            {
                "from_id": library_source_id,
                "to_id": "chk:library",
                "type": "CONTAINS",
                "properties": {
                    "domain": "library",
                    "source_ids": ["src://shared"],
                    "chunk_ids": ["chk:library"],
                },
            },
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:it",
                "embedding": deterministic_embedding("it"),
                "metadata": {"source_url": "src://shared", "domain": "it"},
            },
            {
                "chunk_id": "chk:library",
                "embedding": deterministic_embedding("library"),
                "metadata": {"source_url": "src://shared", "domain": "library"},
            },
        ]
    )
    before_node = graph.get_node(it_source_id)
    before_edge = dict(graph._edges[(it_source_id, "chk:it", "CONTAINS")])
    before_vector = vector.get_vector_metadata("chk:it")
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
        registry=registry,
    )

    result = job.run(
        "library",
        "library-rev",
        stale_source_urls=["src://shared"],
    )

    assert result.status == "ready"
    assert state_store.get("it") == it_state
    assert graph.get_node(it_source_id) == before_node
    assert graph._edges[(it_source_id, "chk:it", "CONTAINS")] == before_edge
    assert vector.get_vector_metadata("chk:it") == before_vector


def test_offline_projection_stale_cleanup_audit_redacts_and_bounds_ids() -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    secret_url = (
        "s3://bucket/doc.pdf?X-Amz-Signature=deadbeefcafe"
        "&X-Amz-Credential=AKIAEXAMPLE&versionId=42"
    )
    graph.upsert_nodes(
        [
            {
                "node_id": "chk:secret",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:secret",
                    "source_url": secret_url,
                    "domain": "it",
                },
            }
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:secret",
                "embedding": deterministic_embedding("secret"),
                "metadata": {"source_url": secret_url, "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
    )

    job._audit_retention(
        "it",
        "job-1",
        "projection-1",
        secret_url,
        [f"chk:{index}" for index in range(200)],
    )

    event = job.audit_log[-1]
    assert event["event"] == "projection_stale_cleanup"
    assert "deadbeefcafe" not in event["source_url"]
    assert "AKIAEXAMPLE" not in event["source_url"]
    assert "versionId=42" in event["source_url"]
    assert event["chunk_ids_total"] == 200
    assert len(event["chunk_ids_sample"]) == 20
    assert event["chunk_ids_truncated"] is True
    assert "chunk_ids" not in event


def test_offline_projection_stale_cleanup_requires_registry() -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    graph.upsert_nodes(
        [
            {
                "node_id": "chk:gone",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:gone",
                    "source_url": "src://gone",
                    "domain": "it",
                },
            }
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:gone",
                "embedding": deterministic_embedding("gone"),
                "metadata": {"source_url": "src://gone", "domain": "it"},
            }
        ]
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
        registry=None,
    )

    result = job.run("it", "data-1", stale_source_urls=["src://gone"])

    assert result.status == "failed"
    assert "DocumentRegistry" in (result.last_error or "")
    assert graph.get_node("chk:gone") is not None
    assert vector.get_vector_metadata("chk:gone") is not None


def test_offline_projection_stale_error_does_not_leak_presigned_url(tmp_path: Path) -> None:
    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    secret_url = "https://acct.blob.core.windows.net/d/doc.pdf?sv=2024&sig=deadbeefcafe&se=2030"
    registry = DocumentRegistry(tmp_path / "documents.db")
    registry.upsert(
        Document(
            source_id="",
            source_url=secret_url,
            domain="it",
            doc_type="txt",
            content_hash="content-secret",
        )
    )
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
        registry=registry,
    )

    result = job.run("it", "data-1", stale_source_urls=[secret_url])

    assert result.status == "failed"
    assert "deadbeefcafe" not in (result.last_error or "")
    assert not [event for event in job.audit_log if "deadbeefcafe" in json.dumps(event)]


def test_safe_error_redacts_embedded_presigned_url() -> None:
    secret_url = "https://acct.blob.core.windows.net/d/doc.pdf?sv=2024&sig=deadbeefcafe&se=2030"

    safe = _safe_error(RuntimeError(f"ошибка чтения {secret_url} для домена it"))

    assert "deadbeefcafe" not in safe
    assert "sv=2024" in safe


def test_offline_projection_retention_rechecks_active_under_lock() -> None:
    class RacingRegistry:
        """Source становится active после первой (вне lock) проверки."""

        def __init__(self) -> None:
            self.calls = 0
            self.locked = 0
            self._lock = threading.RLock()

        @contextmanager
        def source_lock(self, domain: str, source_url: str) -> Any:
            with self._lock:
                self.locked += 1
                yield

        def latest_active(self, domain: str, source_url: str) -> dict[str, Any] | None:
            self.calls += 1
            return None if self.calls == 1 else {"source_url": source_url, "domain": domain}

    state_store = InMemoryProjectionStateStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    graph.upsert_nodes(
        [
            {
                "node_id": "chk:racing",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:racing",
                    "source_url": "src://racing",
                    "domain": "it",
                },
            }
        ]
    )
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:racing",
                "embedding": deterministic_embedding("racing"),
                "metadata": {"source_url": "src://racing", "domain": "it"},
            }
        ]
    )
    registry = RacingRegistry()
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [],
        registry=cast(Any, registry),
    )

    result = job.run("it", "data-1", stale_source_urls=["src://racing"])

    assert result.status == "failed"
    assert "active" in (result.last_error or "")
    assert registry.locked == 1
    assert graph.get_node("chk:racing") is not None
    assert vector.get_vector_metadata("chk:racing") is not None


def test_offline_projection_cli_requires_existing_ingestion_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    units_path = tmp_path / "units.jsonl"
    units_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.projection.build_projection_state_store",
        lambda _path=None: InMemoryProjectionStateStore(),
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_graph_store",
        lambda *a, **k: InMemoryGraphStore(),
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_vector_store",
        lambda *a, **k: InMemoryVectorStore(),
    )
    monkeypatch.setenv("INGESTION_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "graphrag-projection",
            "--units",
            str(units_path),
            "--domain",
            "it",
            "--data-revision",
            "data-1",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        projection_main()

    assert exit_info.value.code == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"]["status"] == "failed"
    assert "ingestion_registry_unavailable" in printed["state"]["last_error"]


def test_offline_projection_cli_rejects_registry_revision_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    units_path = tmp_path / "units.jsonl"
    units_path.write_text("", encoding="utf-8")
    registry = DocumentRegistry(_ingestion_db(tmp_path))
    registry.upsert(
        Document(
            source_id="",
            source_url="src://doc",
            domain="it",
            doc_type="txt",
            content_hash="content-1",
        )
    )
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.projection.build_projection_state_store",
        lambda _path=None: InMemoryProjectionStateStore(),
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_graph_store",
        lambda *a, **k: InMemoryGraphStore(),
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_vector_store",
        lambda *a, **k: InMemoryVectorStore(),
    )
    monkeypatch.setenv("INGESTION_DB_PATH", str(tmp_path / "ingestion.db"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "graphrag-projection",
            "--units",
            str(units_path),
            "--domain",
            "it",
            "--data-revision",
            "stale-revision",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        projection_main()

    assert exit_info.value.code == 2
    printed = json.loads(capsys.readouterr().out)
    assert "data_revision_mismatch" in printed["state"]["last_error"]


def test_offline_projection_cli_exits_nonzero_when_lease_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class StolenLeaseStore(InMemoryProjectionStateStore):
        def renew(
            self,
            domain: str,
            expected_job_id: str,
            lease_seconds: float,
            now: float | None = None,
        ) -> bool:
            self.put(
                ProjectionState(
                    domain=domain,
                    data_revision="other-revision",
                    projection_revision="other-projection",
                    config_fingerprint="other-config",
                    status="ready",
                    job_id="other-job",
                )
            )
            return False

    units_path = tmp_path / "units.jsonl"
    units_path.write_text("", encoding="utf-8")
    state_store = StolenLeaseStore()
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.projection.build_projection_state_store",
        lambda _path=None: state_store,
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_graph_store",
        lambda *a, **k: graph,
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_vector_store",
        lambda *a, **k: vector,
    )
    monkeypatch.setenv("INGESTION_DB_PATH", str(_ingestion_db(tmp_path)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "graphrag-projection",
            "--units",
            str(units_path),
            "--domain",
            "it",
            "--data-revision",
            "data-1",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        projection_main()

    assert exit_info.value.code == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"]["status"] == "pending"
    assert printed["state"]["data_revision"] == "data-1"
    assert printed["state"]["job_id"] != "other-job"
    assert state_store.get("it").job_id == "other-job"


def test_offline_projection_cli_rebuilds_stale_ready_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_store = InMemoryProjectionStateStore()
    state_store.put(
        ProjectionState(
            domain="it",
            data_revision="other-revision",
            projection_revision="other-projection",
            config_fingerprint="other-config",
            status="ready",
            job_id="other-job",
        )
    )
    units_path = tmp_path / "units.jsonl"
    units_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.projection.build_projection_state_store",
        lambda _path=None: state_store,
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_graph_store",
        lambda *a, **k: InMemoryGraphStore(),
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_vector_store",
        lambda *a, **k: InMemoryVectorStore(),
    )
    monkeypatch.setenv("INGESTION_DB_PATH", str(_ingestion_db(tmp_path)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "graphrag-projection",
            "--units",
            str(units_path),
            "--domain",
            "it",
            "--data-revision",
            "data-1",
        ],
    )

    projection_main()

    printed = json.loads(capsys.readouterr().out)
    assert printed["state"]["status"] == "ready"
    assert printed["state"]["data_revision"] == "data-1"
    assert printed["state"]["job_id"] != "other-job"


def test_offline_projection_cli_exits_nonzero_on_failed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_store = InMemoryProjectionStateStore()
    units_path = tmp_path / "units.jsonl"
    units_path.write_text("{not json}\n", encoding="utf-8")
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.projection.build_projection_state_store",
        lambda _path=None: state_store,
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_graph_store",
        lambda *a, **k: InMemoryGraphStore(),
    )
    monkeypatch.setattr(
        "graphrag_proto.retrieval.adapters.factory.build_vector_store",
        lambda *a, **k: InMemoryVectorStore(),
    )
    monkeypatch.setenv("INGESTION_DB_PATH", str(_ingestion_db(tmp_path)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "graphrag-projection",
            "--units",
            str(units_path),
            "--domain",
            "it",
            "--data-revision",
            "data-1",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        projection_main()

    assert exit_info.value.code == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"]["status"] == "failed"
    assert printed["state"]["data_revision"] == "data-1"
