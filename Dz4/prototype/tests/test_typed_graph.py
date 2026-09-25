from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.ingestion_service.pipeline.chunker import Chunker
from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    Analyzer,
    ChunkStage,
    CommitStage,
    ContractStage,
    DedupStage,
    EmbedStage,
    ExtractStage,
    IngestStage,
    NormalizeStage,
    PipelineContext,
    ValidateStage,
    _context_node_id,
    _identity_key,
    _source_node_id,
)
from graphrag_proto.ingestion_service.readers.registry import TxtReader
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.neo4j import (
    Neo4jGraphStore,
    _upsert_edges,
    _upsert_nodes,
    _upsert_vectors,
)
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.pipeline import QueryPipeline

CONTAINER_IT = (
    "требование граф знаний индексировать документы\n"
    "контракт API сервис версия база данных реляционная\n"
)

_AI_PROFILE: dict[str, Any] = {
    "profile": {"name": "it"},
    "extraction": {
        "llm_enabled": True,
        "prompt_template": {
            "id": "extract_context_v1",
            "system": "Extract generic context nodes and links.",
            "user": "Return JSON with tags and links.",
        },
    },
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

_AI_CONTEXT = {
    "tags": [
        {
            "tag_id": "tag:it:requirement",
            "canonical_name": "требование",
            "name": "требование",
            "aliases": ["правило"],
            "origin": "ai",
            "confidence": 0.82,
            "properties": {"language": "ru"},
        },
        {
            "tag_id": "tag:it:indexing",
            "canonical_name": "индексирование",
            "name": "индексирование",
            "aliases": ["индекс"],
            "origin": "ai",
            "confidence": 0.76,
            "properties": {"language": "ru"},
        },
    ],
    "links": [
        {
            "from": "требование",
            "to": "индексирование",
            "kind": "depends_on",
        }
    ],
}


def _ai_profile() -> dict[str, Any]:
    return deepcopy(_AI_PROFILE)


def _build_analyzer(
    tmp_path: Path,
    graph: InMemoryGraphStore,
    vector: InMemoryVectorStore,
    *,
    llm: Any | None = None,
    profile_fetcher: Any | None = None,
) -> tuple[Analyzer, DocumentRegistry]:
    registry = DocumentRegistry(tmp_path / "context.db")
    analyzer = Analyzer(
        [
            IngestStage({"txt": TxtReader()}),
            ChunkStage(profile_fetcher=profile_fetcher),
            EmbedStage(),
            ExtractStage(
                llm=llm,
                profile_fetcher=profile_fetcher,
                optional_failure=True,
            ),
            NormalizeStage(""),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(
                registry,
                graph_store=graph,
                vector_store=vector,
                profile_fetcher=profile_fetcher,
                graph_optional=True,
            ),
        ]
    )
    return analyzer, registry


def _run(
    analyzer: Analyzer,
    src: Path,
    *,
    tags: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PipelineContext:
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        source_path=str(src),
        tags=[] if tags is None else tags,
        links=[] if links is None else links,
        metadata={} if metadata is None else metadata,
    )
    analyzer.run(ctx)
    return ctx


class _RecordingLLM(FakeLLM):
    def __init__(self, text: str) -> None:
        super().__init__(text=text, is_fake=False)
        self.calls: list[tuple[str, str]] = []

    def generate(self, prompt: str, system: str = "", stream: bool = True) -> Any:
        self.calls.append((prompt, system))
        yield from self._deltas


class _NoSchemaGraph(InMemoryGraphStore):
    def ensure_schema(self, node_types: list[dict[str, Any]]) -> None:
        raise AssertionError("runtime ingest must not provision ontology schema")


class _RaisingLLM:
    is_fake = False

    def generate(self, prompt: str, system: str = "", stream: bool = True) -> Any:
        raise RuntimeError("LLM failed")
        yield


def test_identity_key_normalizes_unicode_case_and_spaces() -> None:
    assert _identity_key("  Требования\tЁлка  ") == "требования елка"
    assert _context_node_id("it", {"canonical_name": " Требования "}) == _context_node_id(
        "it", {"canonical_name": "требования"}
    )
    assert _context_node_id("it", {"tag_id": "tag:it:stable"}) == "tag:it:stable"


def test_extract_llm_produces_generic_context_nodes_and_edge(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["требование индексировать документы"],
    )

    ExtractStage(
        llm=FakeLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_profile(),
    ).run(ctx)

    assert {entity["type"] for entity in ctx.entities} == {"ContextNode"}
    assert {entity["tag_id"] for entity in ctx.entities} == {
        "tag:it:requirement",
        "tag:it:indexing",
    }
    assert all(entity["origin"] == "ai" for entity in ctx.entities)
    assert {entity["confidence"] for entity in ctx.entities} == {0.82, 0.76}
    assert all(entity["extractor_version"] == "llm:extract_context_v1" for entity in ctx.entities)
    assert {
        "from": "требование",
        "to": "индексирование",
        "kind": "depends_on",
        "origin": "ai",
    } in [dict(edge) for edge in ctx.entity_edges]


def test_llm_origin_is_forced_to_ai(monkeypatch: Any) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_AI_CONTEXT)
    payload["tags"][0]["origin"] = "user"
    payload["links"][0]["origin"] = "user"
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["требование индексировать документы"],
    )

    ExtractStage(
        llm=FakeLLM(text=json.dumps(payload, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_profile(),
    ).run(ctx)

    assert all(entity["origin"] == "ai" for entity in ctx.entities)
    assert all(edge["origin"] == "ai" for edge in ctx.entity_edges)


def test_normalize_reuses_domain_tag_id_for_resolved_alias(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        NormalizeStage,
        "_resolve",
        lambda self, term, domain: "Quicksort",
    )
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
    )
    ctx.entities = [
        {"name": "Quicksort", "canonical": "Quicksort", "origin": "ai"},
        {"name": "Быстрая сортировка", "canonical": "Быстрая сортировка", "origin": "ai"},
    ]

    NormalizeStage("http://glossary").run(ctx)

    assert {entity["tag_id"] for entity in ctx.entities} == {"tag:it:quicksort"}


def test_context_edges_accept_tag_ids_and_preserve_ai_provenance(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_AI_CONTEXT)
    payload["links"] = [
        {
            "from_id": "tag:it:requirement",
            "to_id": "tag:it:indexing",
            "kind": "depends_on",
            "origin": "ai",
            "confidence": 0.73,
            "source_ids": ["src://d.txt"],
            "properties": {"weight": 0.9},
        }
    ]
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["требование индексировать документы"],
    )

    ExtractStage(
        llm=FakeLLM(text=json.dumps(payload, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_profile(),
    ).run(ctx)

    assert ctx.entity_edges == [
        {
            "from_id": "tag:it:requirement",
            "to_id": "tag:it:indexing",
            "kind": "depends_on",
            "origin": "ai",
            "confidence": 0.73,
            "source_ids": ["src://d.txt"],
            "properties": {"weight": 0.9},
        }
    ]


def test_extract_llm_prompt_contains_each_document_chunk(monkeypatch: Any) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_AI_CONTEXT)
    payload["links"] = []
    chunks = [CONTAINER_IT, "контракт API версия базы данных"]
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=chunks,
    )
    llm = _RecordingLLM(text=json.dumps(payload, ensure_ascii=False))

    ExtractStage(llm=llm, profile_fetcher=lambda _domain: _ai_profile()).run(ctx)

    assert len(llm.calls) == len(chunks)
    prompt0, _ = llm.calls[0]
    assert "граф знаний индексировать документы" in prompt0
    assert "контракт API версия базы данных" not in prompt0
    prompt1, _ = llm.calls[1]
    assert "контракт API версия базы данных" in prompt1
    assert all("Текст документа для извлечения" in prompt for prompt, _ in llm.calls)


def test_same_canonical_with_distinct_tag_ids_remains_distinct() -> None:
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        entities=[
            {
                "tag_id": "tag:it:shared-a",
                "canonical_name": "общий термин",
                "type": "ContextNode",
                "source_ids": ["src://a"],
                "chunk_ids": ["chk:a"],
            },
            {
                "tag_id": "tag:it:shared-b",
                "canonical_name": "общий термин",
                "type": "ContextNode",
                "source_ids": ["src://b"],
                "chunk_ids": ["chk:b"],
            },
        ],
    )

    DedupStage().run(ctx)

    assert len(ctx.entities) == 2
    assert {entity["tag_id"] for entity in ctx.entities} == {
        "tag:it:shared-a",
        "tag:it:shared-b",
    }


def test_same_tag_id_merges_context_provenance() -> None:
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        entities=[
            {
                "tag_id": "tag:it:shared",
                "canonical_name": "общий термин",
                "type": "ContextNode",
                "origin": "user",
                "source_ids": ["src://a"],
                "chunk_ids": ["chk:a"],
                "aliases": ["первый вариант"],
            },
            {
                "tag_id": "tag:it:shared",
                "canonical_name": "общий термин",
                "type": "ContextNode",
                "origin": "ai",
                "source_ids": ["src://b"],
                "chunk_ids": ["chk:b"],
                "aliases": ["второй вариант"],
            },
        ],
    )

    DedupStage().run(ctx)

    assert len(ctx.entities) == 1
    node = ctx.entities[0]
    assert node["source_ids"] == ["src://a", "src://b"]
    assert node["chunk_ids"] == ["chk:a", "chk:b"]
    assert node["aliases"] == ["первый вариант", "второй вариант"]


def test_extract_fallback_does_not_require_profile_or_llm(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("EXTRACT_LLM", raising=False)
    stage = ExtractStage(
        profile_fetcher=lambda _domain: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["fallback entity"],
    )

    stage.run(ctx)

    assert ctx.entities
    assert {entity["type"] for entity in ctx.entities} == {"ContextNode"}
    assert {entity["tag_id"] for entity in ctx.entities} == {"tag:it:fallback", "tag:it:entity"}
    assert all(entity["extractor_version"] == "deterministic:v1" for entity in ctx.entities)


def test_primitive_ingest_without_profile_ai_or_optional_enrichment(
    tmp_path: Path,
) -> None:
    source = tmp_path / "primitive.txt"
    source.write_text("primitive document", encoding="utf-8")
    graph = _NoSchemaGraph()
    vector = InMemoryVectorStore()
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://primitive.txt",
        source_path=str(source),
    )

    IngestStage({"txt": TxtReader()}).run(ctx)
    ChunkStage().run(ctx)
    EmbedStage().run(ctx)
    CommitStage(
        DocumentRegistry(tmp_path / "primitive.db"),
        graph_store=graph,
        vector_store=vector,
    ).run(ctx)

    source_id = _source_node_id("it", "src://primitive.txt")
    chunk_ids = graph.list_chunk_ids_of_source(source_id)
    assert ctx.commit_applied is True
    assert graph.get_node(source_id) is not None
    assert chunk_ids
    assert all(graph.get_node(chunk_id)["_labels"] == ["Chunk"] for chunk_id in chunk_ids)
    assert all(node["labels"] != ["ContextNode"] for node in graph._nodes.values())
    embedding = DeterministicEmbedder().embed("primitive document", "it")
    hits = vector.vector_search(embedding, top_k=5, domain="it")
    assert [hit["chunk_id"] for hit in hits] == chunk_ids
    assert all(hit["context_ids"] == [] for hit in hits)


def test_commit_persists_generic_context_graph_without_schema(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    graph = _NoSchemaGraph()
    vector = InMemoryVectorStore()
    analyzer, _ = _build_analyzer(
        tmp_path,
        graph,
        vector,
        llm=FakeLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_profile(),
    )
    source = tmp_path / "d.txt"
    source.write_text(CONTAINER_IT, encoding="utf-8")

    ctx = _run(analyzer, source)

    assert ctx.commit_applied is True
    requirement = graph.get_node("tag:it:requirement")
    indexing = graph.get_node("tag:it:indexing")
    assert requirement is not None
    assert indexing is not None
    assert requirement["_labels"] == ["ContextNode"]
    assert indexing["_labels"] == ["ContextNode"]
    assert requirement["tag_id"] == "tag:it:requirement"
    assert indexing["tag_id"] == "tag:it:indexing"
    assert requirement["origin"] == "ai"
    assert requirement["properties"] == {"language": "ru"}
    assert requirement["source_ids"] == ["src://d.txt"]
    assert requirement["chunk_ids"]
    edge_key = ("tag:it:requirement", "tag:it:indexing", "DEPENDS_ON")
    assert edge_key in graph._edges
    assert graph._edges[edge_key]["kind"] == "depends_on"
    assert any(
        edge[0].startswith("chk:")
        and edge[1] == "tag:it:requirement"
        and edge[2] == "MENTIONS"
        for edge in graph._edges
    )
    embedding = DeterministicEmbedder().embed(CONTAINER_IT, "it")
    hits = vector.vector_search(embedding, top_k=10, domain="it")
    assert any(
        set(hit["context_ids"]) >= {"tag:it:requirement", "tag:it:indexing"}
        for hit in hits
    )


def test_manual_tags_and_links_preserve_generic_provenance(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("EXTRACT_LLM", raising=False)
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    analyzer, _ = _build_analyzer(tmp_path, graph, vector)
    source = tmp_path / "manual.txt"
    source.write_text(CONTAINER_IT, encoding="utf-8")
    tags = [
        {
            "tag_id": "tag:it:quicksort",
            "canonical_name": "Quicksort",
            "aliases": ["Быстрая сортировка"],
            "origin": "user",
            "confidence": 0.99,
            "properties": {"language": "en"},
        },
        {
            "tag_id": "tag:it:sorting",
            "canonical_name": "Sorting",
            "origin": "user",
            "confidence": 0.91,
        },
    ]
    links = [
        {
            "from_id": "tag:it:quicksort",
            "to_id": "tag:it:sorting",
            "kind": "depends_on",
            "origin": "user",
            "confidence": 0.88,
            "source_ids": ["src://d.txt"],
            "properties": {"weight": 0.7},
        }
    ]

    _run(analyzer, source, tags=tags, links=links, metadata={"custom": {"kind": "manual"}})

    node = graph.get_node("tag:it:quicksort")
    assert node is not None
    assert node["_labels"] == ["ContextNode"]
    assert node["canonical_name"] == "Quicksort"
    assert node["aliases"] == ["Быстрая сортировка"]
    assert node["origin"] == "user"
    assert node["confidence"] == 0.99
    assert node["source_ids"] == ["src://d.txt"]
    assert node["properties"] == {"language": "en"}
    edge_key = ("tag:it:quicksort", "tag:it:sorting", "DEPENDS_ON")
    assert edge_key in graph._edges
    assert graph._edges[edge_key] == {
        "kind": "depends_on",
        "weight": 0.7,
        "domain": "it",
        "origin": "user",
        "confidence": 0.88,
        "source_ids": ["src://d.txt"],
    }
    embedding = DeterministicEmbedder().embed(CONTAINER_IT, "it")
    hit = next(
        row
        for row in vector.vector_search(embedding, top_k=10, domain="it")
        if "tag:it:quicksort" in row["context_ids"]
    )
    assert {"tag:it:quicksort", "tag:it:sorting"}.issubset(hit["tag_ids"])
    assert hit["custom"] == {"kind": "manual"}


def test_manual_tag_is_not_overwritten_by_ai_for_same_tag_id(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    analyzer, _ = _build_analyzer(
        tmp_path,
        graph,
        vector,
        llm=FakeLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_profile(),
    )
    source = tmp_path / "mixed.txt"
    source.write_text(CONTAINER_IT, encoding="utf-8")

    _run(
        analyzer,
        source,
        tags=[
            {
                "tag_id": "tag:it:requirement",
                "canonical_name": "ручное требование",
                "origin": "user",
                "confidence": 1.0,
            }
        ],
    )

    node = graph.get_node("tag:it:requirement")
    assert node is not None
    assert node["canonical_name"] == "ручное требование"
    assert node["origin"] == "user"
    assert node["confidence"] == 1.0


def test_optional_ai_failure_does_not_block_document_persistence(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    graph = _NoSchemaGraph()
    vector = InMemoryVectorStore()
    analyzer, _ = _build_analyzer(
        tmp_path,
        graph,
        vector,
        llm=_RaisingLLM(),
        profile_fetcher=lambda _domain: _ai_profile(),
    )
    source = tmp_path / "ai-failure.txt"
    source.write_text(CONTAINER_IT, encoding="utf-8")

    ctx = _run(analyzer, source)

    assert ctx.commit_applied is True
    assert ctx.enrichment_degraded is True
    assert ctx.enrichment_error
    assert graph.get_node(_source_node_id("it", "src://d.txt")) is not None
    assert graph.list_chunk_ids_of_source(_source_node_id("it", "src://d.txt"))
    assert vector._vectors
    assert all(node.get("origin") != "ai" for node in graph._nodes.values())


def test_fake_llm_uses_deterministic_context_fallback(monkeypatch: Any) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["fake extraction fallback entity"],
    )

    ExtractStage(
        llm=FakeLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False)),
        profile_fetcher=lambda _domain: (_ for _ in ()).throw(RuntimeError("offline")),
    ).run(ctx)

    assert {entity["type"] for entity in ctx.entities} == {"ContextNode"}
    assert all(entity["extractor_version"] == "deterministic:v1" for entity in ctx.entities)


def test_real_llm_failure_is_not_converted_to_fallback(monkeypatch: Any) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["entity"],
    )

    with pytest.raises(RuntimeError, match="LLM failed"):
        ExtractStage(
            llm=_RaisingLLM(),
            profile_fetcher=lambda _domain: _ai_profile(),
        ).run(ctx)


def test_real_llm_invalid_json_is_not_converted_to_fallback(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["entity"],
    )

    with pytest.raises(ValueError, match="валидный JSON"):
        ExtractStage(
            llm=FakeLLM(text="not-json", is_fake=False),
            profile_fetcher=lambda _domain: _ai_profile(),
        ).run(ctx)


def test_profile_without_llm_capability_uses_deterministic_fallback(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    llm = _RecordingLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False))
    ctx = PipelineContext(
        job_id="j",
        domain="library",
        doc_type="txt",
        source_url="src://book.txt",
        chunks=["library fallback entity"],
    )

    ExtractStage(
        llm=llm,
        profile_fetcher=lambda _domain: {"profile": {"name": "library"}},
    ).run(ctx)

    assert {entity["type"] for entity in ctx.entities} == {"ContextNode"}
    assert all(entity["tag_id"].startswith("tag:library:") for entity in ctx.entities)
    assert llm.calls == []


def test_llm_context_description_does_not_echo_chunk_text(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = {
        "tags": [
            {
                "tag_id": "tag:it:raw",
                "canonical_name": "сырой текст",
                "description": "Цитата: сырой текст",
            }
        ],
        "links": [],
    }
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["сырой текст"],
    )

    ExtractStage(
        llm=FakeLLM(text=json.dumps(payload, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_profile(),
    ).run(ctx)

    assert ctx.entities[0].get("description") is None


def test_llm_accepts_dynamic_context_payload_with_optional_legacy_hints(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    profile = _ai_profile()
    profile["ontology"] = {"node_types": [], "edge_types": []}
    payload = {
        "tags": [
            {
                "tag_id": "tag:it:custom",
                "canonical_name": "Custom",
                "aliases": ["Пользовательский"],
                "origin": "ai",
                "confidence": 0.64,
                "properties": {"custom": True},
            }
        ],
        "links": [],
    }
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["custom"],
    )

    ExtractStage(
        llm=FakeLLM(text=json.dumps(payload, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: profile,
    ).run(ctx)

    assert ctx.entities[0]["type"] == "ContextNode"
    assert ctx.entities[0]["tag_id"] == "tag:it:custom"
    assert ctx.entities[0]["properties"] == {"custom": True}


def test_deterministic_fallback_keeps_repeated_context_chunk_links(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("EXTRACT_LLM", raising=False)
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["alpha alpha", "alpha"],
    )

    ExtractStage(
        profile_fetcher=lambda _domain: (_ for _ in ()).throw(RuntimeError("offline"))
    ).run(ctx)

    assert len(ctx.entities) == 1
    assert ctx.entities[0]["tag_id"] == "tag:it:alpha"
    assert len(ctx.entities[0]["chunk_ids"]) == 2


def test_graph_store_merges_context_provenance() -> None:
    graph = InMemoryGraphStore()
    node_id = "tag:it:shared"
    graph.upsert_nodes(
        [
            {
                "node_id": node_id,
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": node_id,
                    "source_ids": ["src://a"],
                    "chunk_ids": ["chk:a"],
                    "variants": ["term"],
                },
            }
        ]
    )
    graph.upsert_nodes(
        [
            {
                "node_id": node_id,
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": node_id,
                    "source_ids": ["src://b"],
                    "chunk_ids": ["chk:b"],
                    "variants": ["shared"],
                },
            }
        ]
    )

    node = graph.get_node(node_id)
    assert node is not None
    assert node["_labels"] == ["ContextNode"]
    assert node["tag_id"] == node_id
    assert node["source_ids"] == ["src://a", "src://b"]
    assert node["chunk_ids"] == ["chk:a", "chk:b"]
    assert node["variants"] == ["term", "shared"]


def test_llm_repeated_tag_merges_chunk_ids(monkeypatch: Any) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=["требование", "требование"],
    )

    ExtractStage(
        llm=FakeLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_profile(),
    ).run(ctx)
    DedupStage().run(ctx)

    matching = [entity for entity in ctx.entities if entity["tag_id"] == "tag:it:requirement"]
    assert len(matching) == 1
    assert len(matching[0]["chunk_ids"]) == 2


def test_generic_noop_reingest_keeps_context_nodes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    llm = _RecordingLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False))
    analyzer, _ = _build_analyzer(
        tmp_path,
        graph,
        vector,
        llm=llm,
        profile_fetcher=lambda _domain: _ai_profile(),
    )
    source = tmp_path / "d.txt"
    source.write_text(CONTAINER_IT, encoding="utf-8")

    _run(analyzer, source)
    calls_after_first = list(llm.calls)
    node_ids_before = set(graph._nodes)
    vector_ids_before = set(vector._vectors)
    second = _run(analyzer, source)

    assert second.noop is True
    assert set(graph._nodes) == node_ids_before
    assert set(vector._vectors) == vector_ids_before
    assert llm.calls == calls_after_first


class _InjectedChunker(Chunker):
    def chunk(self, text: str) -> list[str]:
        return ["INJECTED"]


def test_injected_chunker_precedes_profile_chunker(tmp_path: Path) -> None:
    source = tmp_path / "chunk.txt"
    source.write_text("profile text", encoding="utf-8")
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        source_path=str(source),
    )

    IngestStage({"txt": TxtReader()}).run(ctx)
    ChunkStage(
        _InjectedChunker(),
        profile_fetcher=lambda _domain: _ai_profile(),
    ).run(ctx)

    assert ctx.chunks == ["INJECTED"]


def test_profile_is_loaded_once_for_direct_analyzer(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    calls: list[str] = []

    def fetcher(domain: str) -> dict[str, Any]:
        calls.append(domain)
        return _ai_profile()

    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    analyzer, _ = _build_analyzer(
        tmp_path,
        graph,
        vector,
        llm=FakeLLM(text=json.dumps(_AI_CONTEXT, ensure_ascii=False), is_fake=False),
        profile_fetcher=fetcher,
    )
    source = tmp_path / "once.txt"
    source.write_text(CONTAINER_IT, encoding="utf-8")

    _run(analyzer, source)

    assert calls == ["it"]


class _StaticProfileLoader:
    def __init__(self, profile: dict[str, Any]) -> None:
        self._profile = profile

    def active_domain(self) -> str:
        return "it"

    def load(self, domain: str | None = None) -> dict[str, Any]:
        return self._profile


class _OrderedVectorStore(InMemoryVectorStore):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        self.events.append("vector")
        return super().vector_search(embedding, top_k=top_k, domain=domain)


class _ExpansionGraph(InMemoryGraphStore):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events
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
        self.events.append("expand")
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
                "node_id": "tag:it:algorithms",
                "canonical_name": "Алгоритмы",
                "path": [*context_ids, "tag:it:algorithms"],
                "depth": 1,
                "kind": "parent",
                "origin": "user",
                "confidence": 0.9,
                "source_ids": ["src://vector"],
                "chunk_ids": ["chk:vector"],
                "domain": "it",
            }
        ]


class _FailingExpansionGraph(_ExpansionGraph):
    def expand(
        self,
        context_ids: list[str],
        *,
        direction: str = "parent",
        max_depth: int = 2,
        max_fanout: int = 8,
        max_nodes: int = 32,
    ) -> list[dict[str, Any]]:
        super().expand(
            context_ids,
            direction=direction,
            max_depth=max_depth,
            max_fanout=max_fanout,
            max_nodes=max_nodes,
        )
        raise RuntimeError("graph unavailable")


def _pipeline(
    profile: dict[str, Any],
    graph: InMemoryGraphStore,
    vector: InMemoryVectorStore,
) -> QueryPipeline:
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="answer"),
        profile_loader=_StaticProfileLoader(profile),
    )


def _seed_vector_context(vector: InMemoryVectorStore) -> None:
    embedder = DeterministicEmbedder()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:vector",
                "embedding": embedder.embed("Quicksort", "it"),
                "metadata": {
                    "text": "Quicksort",
                    "source_url": "src://vector",
                    "domain": "it",
                    "context_ids": ["tag:it:quicksort"],
                },
            }
        ]
    )


def test_vector_results_seed_bounded_expansion_before_graph() -> None:
    events: list[str] = []
    profile = _ai_profile()
    graph = _ExpansionGraph(events)
    vector = _OrderedVectorStore(events)
    _seed_vector_context(vector)

    done = _pipeline(profile, graph, vector).run(
        "Quicksort",
        domain="it",
        generate=False,
        trace=True,
    )

    assert events == ["vector", "expand"]
    assert graph.expansion_calls == [
        (
            ["tag:it:quicksort"],
            {"direction": "parent", "max_depth": 2, "max_fanout": 4, "max_nodes": 8},
        )
    ]
    expansion_trace = next(
        event for event in done["trace"] if event.get("stage") == "graph_expansion"
    )
    assert expansion_trace["seed_chunk_ids"] == ["chk:vector"]
    assert expansion_trace["context_ids"] == ["tag:it:quicksort"]
    assert expansion_trace["paths"] == [
        ["tag:it:quicksort", "tag:it:algorithms"]
    ]
    assert expansion_trace["depths"] == [1]
    assert done["graph_degraded"] is False
    assert {
        "source_url": "src://vector",
        "relevance": 1.0,
        "axis": "graph",
    } in done["sources"]


def test_vector_only_baseline_does_not_call_graph(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "false")
    events: list[str] = []
    graph = _ExpansionGraph(events)
    vector = _OrderedVectorStore(events)
    _seed_vector_context(vector)

    done = _pipeline(_ai_profile(), graph, vector).run(
        "Quicksort",
        domain="it",
        generate=False,
        trace=True,
    )

    assert events == ["vector"]
    assert graph.expansion_calls == []
    graph_trace = next(event for event in done["trace"] if event.get("stage") == "graph")
    assert graph_trace["enabled"] is False
    assert graph_trace["skeleton_rows"] == []
    assert done["graph_degraded"] is False


def test_vector_first_trace_preserves_rank_and_graph_provenance() -> None:
    events: list[str] = []
    graph = _ExpansionGraph(events)
    vector = _OrderedVectorStore(events)
    _seed_vector_context(vector)

    done = _pipeline(_ai_profile(), graph, vector).run(
        "Quicksort",
        domain="it",
        generate=False,
        trace=True,
    )

    vector_trace = next(event for event in done["trace"] if event.get("stage") == "vector")
    assert vector_trace["candidates"] == [
        {
            "chunk_id": "chk:vector",
            "rank": 1,
            "score": 1.0,
            "score_before": 1.0,
        }
    ]
    expansion_trace = next(
        event for event in done["trace"] if event.get("stage") == "graph_expansion"
    )
    assert expansion_trace["origins"] == ["user"]
    assert expansion_trace["confidences"] == [0.9]


def test_graph_expansion_failure_returns_vector_fallback() -> None:
    events: list[str] = []
    graph = _FailingExpansionGraph(events)
    vector = _OrderedVectorStore(events)
    _seed_vector_context(vector)

    done = _pipeline(_ai_profile(), graph, vector).run(
        "Quicksort",
        domain="it",
        generate=False,
        trace=True,
    )

    assert events == ["vector", "expand"]
    assert done["graph_degraded"] is True
    assert any(
        source["axis"] == "vector" and source["source_url"] == "src://vector"
        for source in done["sources"]
    )
    degraded_trace = next(
        event for event in done["trace"] if event.get("stage") == "graph_expansion"
    )
    assert degraded_trace["degraded"] is True


def test_inmemory_expand_preserves_context_provenance_and_budget() -> None:
    graph = InMemoryGraphStore()
    graph.upsert_nodes(
        [
            {
                "node_id": "tag:it:seed",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:seed",
                    "canonical_name": "Seed",
                    "domain": "it",
                    "source_ids": ["src://seed"],
                    "chunk_ids": ["chk:seed"],
                },
            },
            {
                "node_id": "tag:it:first",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:first",
                    "canonical_name": "First",
                    "domain": "it",
                    "origin": "user",
                    "confidence": 0.9,
                    "source_ids": ["src://first"],
                    "chunk_ids": ["chk:first"],
                },
            },
            {
                "node_id": "tag:it:second",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:second",
                    "canonical_name": "Second",
                    "domain": "it",
                    "origin": "ai",
                    "confidence": 0.8,
                    "source_ids": ["src://second"],
                    "chunk_ids": ["chk:second"],
                },
            },
        ]
    )
    graph.upsert_edges(
        [
            {
                "from_id": "tag:it:seed",
                "to_id": "tag:it:first",
                "type": "PARENT",
                "properties": {
                    "kind": "parent",
                    "origin": "user",
                    "confidence": 0.9,
                    "source_ids": ["src://first"],
                },
            },
            {
                "from_id": "tag:it:seed",
                "to_id": "tag:it:second",
                "type": "PARENT",
                "properties": {
                    "kind": "parent",
                    "origin": "ai",
                    "confidence": 0.8,
                    "source_ids": ["src://second"],
                },
            },
        ]
    )

    rows = graph.expand(
        ["tag:it:seed"],
        max_depth=1,
        max_fanout=1,
        max_nodes=1,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == ["tag:it:seed", row["node_id"]]
    assert row["depth"] == 1
    assert row["kind"] == "parent"
    assert row["source_ids"]
    assert row["origin"] in {"user", "ai"}
    assert row["confidence"] == pytest.approx(0.9 if row["origin"] == "user" else 0.8)


class _Runner:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def run(self, cypher: str, parameters: dict[str, Any] | None = None) -> _Runner:
        self.statements.append(cypher)
        return self

    def consume(self) -> _Runner:
        return self

    def data(self) -> list[dict[str, Any]]:
        return []


class _SessionCtx:
    def __init__(self, runner: _Runner) -> None:
        self._runner = runner

    def __enter__(self) -> _Runner:
        return self._runner

    def __exit__(self, *args: object) -> bool:
        return False


def test_neo4j_vector_custom_metadata_is_json_encoded() -> None:
    class Result:
        def consume(self) -> Result:
            return self

    class Runner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def run(self, query: str, parameters: dict[str, Any] | None = None) -> Result:
            self.calls.append((query, dict(parameters or {})))
            return Result()

    runner = Runner()
    _upsert_vectors(
        runner,
        [
            {
                "chunk_id": "chk:1",
                "embedding": [0.1],
                "metadata": {"custom": {"nested": {"x": 1}}},
            }
        ],
    )

    _query, parameters = runner.calls[-1]
    assert isinstance(parameters["props"]["custom"], str)
    assert json.loads(parameters["props"]["custom"]) == {"nested": {"x": 1}}


def test_neo4j_edge_custom_properties_are_json_encoded() -> None:
    class Result:
        def data(self) -> list[dict[str, Any]]:
            return []

        def consume(self) -> Result:
            return self

    class Runner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def run(self, query: str, parameters: dict[str, Any] | None = None) -> Result:
            self.calls.append((query, dict(parameters or {})))
            return Result()

        def consume(self) -> Result:
            return Result()

    runner = Runner()
    _upsert_edges(
        runner,
        [
            {
                "from_id": "tag:it:a",
                "to_id": "tag:it:b",
                "type": "RELATED",
                "properties": {
                    "origin": "user",
                    "properties": {"nested": {"x": 1}},
                },
            }
        ],
    )

    _query, parameters = runner.calls[-1]
    assert json.loads(parameters["properties_value"]) == {"nested": {"x": 1}}
    assert "properties" not in parameters["scalar_properties"]


def test_neo4j_custom_properties_are_json_encoded_and_merged() -> None:
    class Result:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def data(self) -> list[dict[str, Any]]:
            return self._rows

        def consume(self) -> Result:
            return self

    class Runner:
        def __init__(self, existing: dict[str, Any]) -> None:
            self.existing = existing
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def run(self, query: str, parameters: dict[str, Any] | None = None) -> Result:
            self.calls.append((query, dict(parameters or {})))
            if "RETURN n.origin AS origin" in query:
                return Result([self.existing])
            return Result([])

        def consume(self) -> Result:
            return Result([])

    runner = Runner(
        {
            "origin": "ai",
            "properties": json.dumps({"language": "en"}, ensure_ascii=False),
        }
    )
    _upsert_nodes(
        runner,
        [
            {
                "node_id": "tag:it:quicksort",
                "labels": ["ContextNode"],
                "properties": {
                    "origin": "user",
                    "properties": {"author": "bob"},
                },
            }
        ],
    )

    query, parameters = runner.calls[-1]
    assert "SET n.properties = $properties_value" in query
    assert "properties" not in parameters["scalar_properties"]
    assert json.loads(parameters["properties_value"]) == {
        "language": "en",
        "author": "bob",
    }


def test_neo4j_custom_properties_preserve_manual_origin() -> None:
    class Result:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def data(self) -> list[dict[str, Any]]:
            return self._rows

        def consume(self) -> Result:
            return self

    class Runner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def run(self, query: str, parameters: dict[str, Any] | None = None) -> Result:
            self.calls.append((query, dict(parameters or {})))
            if "RETURN n.origin AS origin" in query:
                return Result(
                    [
                        {
                            "origin": "user",
                            "properties": json.dumps({"canonical": "manual"}, ensure_ascii=False),
                        }
                    ]
                )
            return Result([])

        def consume(self) -> Result:
            return Result([])

    runner = Runner()
    _upsert_nodes(
        runner,
        [
            {
                "node_id": "tag:it:quicksort",
                "labels": ["ContextNode"],
                "properties": {
                    "origin": "ai",
                    "properties": {"author": "bot"},
                },
            }
        ],
    )

    _query, parameters = runner.calls[-1]
    assert json.loads(parameters["properties_value"]) == {"canonical": "manual"}


def test_neo4j_upsert_merges_context_provenance() -> None:
    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass")
    runner = _Runner()
    store._session = lambda: _SessionCtx(runner)

    store.upsert_nodes(
        [
            {
                "node_id": "tag:it:quicksort",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:quicksort",
                    "source_ids": ["src://a"],
                    "chunk_ids": ["chk:a"],
                    "variants": ["Quicksort"],
                    "properties": {"language": "en"},
                },
            }
        ]
    )

    statement = runner.statements[-1]
    assert "SET n += CASE WHEN n.origin = 'user'" in statement
    assert "$incoming_origin" in statement
    assert "SET n.source_ids = coalesce(n.source_ids, [])" in statement
    assert "SET n.chunk_ids = coalesce(n.chunk_ids, [])" in statement
    assert "SET n.variants = coalesce(n.variants, [])" in statement
    assert "SET n.properties = $properties_value" in statement


def test_neo4j_upsert_accepts_dynamic_context_edge() -> None:
    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass")
    runner = _Runner()
    store._session = lambda: _SessionCtx(runner)

    store.upsert_edges(
        [
            {
                "from_id": "tag:it:quicksort",
                "to_id": "tag:it:sorting",
                "type": "DEPENDS_ON",
                "properties": {
                    "kind": "depends_on",
                    "origin": "user",
                    "confidence": 0.9,
                    "source_ids": ["src://a"],
                    "properties": {"weight": 0.7},
                },
            }
        ]
    )

    statement = runner.statements[-1]
    assert "MERGE (a)-[r:DEPENDS_ON]->(b)" in statement
    assert "SET r += CASE WHEN r.origin = 'user'" in statement
    assert "SET r.source_ids = coalesce(r.source_ids, [])" in statement
    assert "SET r.properties = $properties_value" in statement
