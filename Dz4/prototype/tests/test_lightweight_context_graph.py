from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from graphrag_proto.config_service.domain import validate_profile
from graphrag_proto.ingestion_service.app import create_app
from graphrag_proto.ingestion_service.document import Document
from graphrag_proto.ingestion_service.pipeline.orchestrator import CommitStage, PipelineContext
from graphrag_proto.ingestion_service.readers.registry import TxtReader
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.adapters.schemas import normalize_vector_row
from graphrag_proto.retrieval.pipeline import QueryPipeline


class _NoSchemaGraph(InMemoryGraphStore):
    def ensure_schema(self, node_types: list[dict[str, Any]]) -> None:
        raise AssertionError("runtime ingest must not provision ontology schema")


class _StaticProfileLoader:
    def active_domain(self) -> str:
        return "it"

    def load(self, domain: str | None = None) -> dict[str, Any]:
        return {
            "profile": {"name": domain or "it"},
            "retrieval": {
                "graph_search_enabled": True,
                "expansion_direction": "parent",
                "max_depth": 2,
                "max_fanout": 4,
                "max_graph_nodes": 8,
                "graph_boost": 0.2,
            },
            "context_assembly": {"max_tokens": 512},
        }


class _ExpansionGraph(InMemoryGraphStore):
    def __init__(self) -> None:
        super().__init__()
        self.expansion_calls: list[tuple[list[str], dict[str, Any]]] = []

    def expand(
        self,
        context_ids: list[str],
        *,
        direction: str = "parent",
        max_depth: int = 2,
        max_fanout: int = 8,
        max_nodes: int = 32,
    ) -> list[dict[str, Any]]:
        self.expansion_calls.append(
            (
                list(context_ids),
                {
                    "direction": direction,
                    "max_depth": max_depth,
                    "max_fanout": max_fanout,
                    "max_nodes": max_nodes,
                },
            )
        )
        return [
            {
                "node_id": "tag:it:district",
                "canonical_name": "District",
                "path": context_ids + ["tag:it:district"],
                "depth": 1,
                "kind": "parent",
                "source_ids": ["src://doc"],
            }
        ]


def _document(tmp_path: Path) -> Document:
    source = tmp_path / "doc.txt"
    source.write_text("Quicksort and Быстрая сортировка", encoding="utf-8")
    document = TxtReader().read(source, "src://doc", "it")
    assert isinstance(document, Document)
    return document


def _context(document: Document) -> PipelineContext:
    return PipelineContext(
        job_id="job",
        domain="it",
        doc_type="txt",
        source_url=document.source_url,
        document=document,
        chunks=["Quicksort"],
        chunks_meta=[{"index": 0, "chunk_id": "chk:doc:0", "embedding": [0.1, 0.2]}],
        entities=[],
        entity_edges=[],
        profile={"profile": {"name": "it"}, "retrieval": {}},
        profile_loaded=True,
    )


def test_profile_without_ontology_is_valid() -> None:
    assert validate_profile({"profile": {"name": "it"}}) == []


def test_commit_does_not_require_ontology_or_schema(tmp_path: Path) -> None:
    document = _document(tmp_path)
    registry = DocumentRegistry(tmp_path / "primitive.db")
    graph = _NoSchemaGraph()
    vector = InMemoryVectorStore()
    ctx = _context(document)

    CommitStage(registry, graph_store=graph, vector_store=vector).run(ctx)

    assert ctx.commit_applied is True
    assert graph.get_node("chk:doc:0") is not None


def test_vector_baseline_survives_without_graph_store(tmp_path: Path) -> None:
    document = _document(tmp_path)
    registry = DocumentRegistry(tmp_path / "vector-only.db")
    vector = InMemoryVectorStore()
    ctx = _context(document)

    CommitStage(registry, graph_store=None, vector_store=vector).run(ctx)

    assert ctx.commit_applied is True
    assert ctx.graph_projection_status == "disabled"
    assert vector.vector_search(ctx.chunks_meta[0]["embedding"], top_k=1, domain="it")


def test_graph_projection_failure_keeps_vector_baseline(tmp_path: Path) -> None:
    class FailingGraph(InMemoryGraphStore):
        def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
            raise RuntimeError("projection unavailable")

    document = _document(tmp_path)
    registry = DocumentRegistry(tmp_path / "degraded.db")
    graph = FailingGraph()
    vector = InMemoryVectorStore()
    ctx = _context(document)

    CommitStage(
        registry,
        graph_store=graph,
        vector_store=vector,
        graph_optional=True,
    ).run(ctx)

    assert ctx.commit_applied is True
    assert ctx.graph_projection_status == "degraded"
    assert ctx.graph_projection_error
    assert vector.vector_search(ctx.chunks_meta[0]["embedding"], top_k=1, domain="it")


def test_commit_preserves_dynamic_tag_ids_and_links(tmp_path: Path) -> None:
    document = _document(tmp_path)
    registry = DocumentRegistry(tmp_path / "dynamic.db")
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    ctx = _context(document)
    ctx.entities = [
        {
            "tag_id": "tag:it:quicksort",
            "name": "Quicksort",
            "canonical_name": "Quicksort",
            "aliases": ["Быстрая сортировка"],
            "type": "ContextNode",
            "origin": "user",
            "properties": {"language": "en"},
            "sources": ["src://doc"],
            "chunk_ids": ["chk:doc:0"],
        },
        {
            "tag_id": "tag:it:sorting",
            "name": "Sorting",
            "canonical_name": "Sorting",
            "type": "ContextNode",
            "origin": "ai",
            "sources": ["src://doc"],
            "chunk_ids": ["chk:doc:0"],
        },
    ]
    ctx.entity_edges = [
        {
            "from_id": "tag:it:quicksort",
            "to_id": "tag:it:sorting",
            "kind": "related",
            "origin": "user",
            "properties": {"weight": 1.0},
        }
    ]

    CommitStage(registry, graph_store=graph, vector_store=vector).run(ctx)

    node = graph.get_node("tag:it:quicksort")
    assert node is not None
    assert node["canonical_name"] == "Quicksort"
    assert node["aliases"] == ["Быстрая сортировка"]
    assert node["properties"]["language"] == "en"
    assert any(edge[0] == "tag:it:quicksort" and edge[1] == "tag:it:sorting" for edge in graph._edges)


def test_ingest_accepts_optional_tags_and_links(tmp_path: Path, monkeypatch: Any) -> None:
    from graphrag_proto.ingestion_service import app as app_module

    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    monkeypatch.setattr(app_module, "build_graph_store", lambda: graph)
    monkeypatch.setattr(app_module, "build_vector_store", lambda: vector)
    app = create_app(
        upload_dir=tmp_path / "uploads",
        db_path=tmp_path / "api.db",
        glossary_url="",
    )
    payload = {
        "source_url": "src://manual.txt",
        "domain": "it",
        "doc_type": "txt",
        "content": "Quicksort",
        "tags": [
            {
                "tag_id": "tag:it:quicksort",
                "canonical_name": "Quicksort",
                "aliases": ["Быстрая сортировка"],
                "origin": "user",
            },
            {
                "tag_id": "tag:it:sorting",
                "canonical_name": "Sorting",
                "origin": "user",
            },
        ],
        "links": [
            {
                "from_id": "tag:it:quicksort",
                "to_id": "tag:it:sorting",
                "kind": "related",
                "origin": "user",
            }
        ],
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ingestion/documents",
            json=payload,
            headers={"X-API-Key": "changeme"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(
                f"/api/v1/ingestion/jobs/{job_id}",
                headers={"X-API-Key": "changeme"},
            ).json()
            if state["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert state["status"] == "succeeded"
    assert graph.get_node("tag:it:quicksort") is not None
    assert graph.get_node("tag:it:sorting") is not None


def test_vector_metadata_preserves_context_ids() -> None:
    row = normalize_vector_row(
        {
            "chunk_id": "chk:1",
            "score": 0.5,
            "c": {
                "text": "chunk",
                "source_url": "src://doc",
                "domain": "it",
                "context_ids": ["tag:it:quicksort"],
                "custom": {"language": "en"},
            },
        }
    )
    assert row["context_ids"] == ["tag:it:quicksort"]
    assert row["custom"] == {"language": "en"}


def test_vector_results_seed_bounded_graph_expansion(monkeypatch: Any) -> None:
    monkeypatch.delenv("RETRIEVAL_GRAPH_ENABLED", raising=False)
    graph = _ExpansionGraph()
    vector = InMemoryVectorStore()
    embedding = DeterministicEmbedder().embed("Quicksort", "it")
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:1",
                "embedding": embedding,
                "metadata": {
                    "text": "Quicksort",
                    "source_url": "src://doc",
                    "domain": "it",
                    "context_ids": ["tag:it:quicksort"],
                },
            }
        ]
    )
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="answer"),
        profile_loader=_StaticProfileLoader(),
    )

    result = pipe.run("Quicksort", domain="it", generate=False, trace=True)

    assert graph.expansion_calls
    assert graph.expansion_calls[0][0] == ["tag:it:quicksort"]
    assert any(event.get("stage") == "graph_expansion" for event in result["trace"])
    assert result["graph_degraded"] is False
