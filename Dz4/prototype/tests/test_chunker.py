"""Chunker: ABC, SlidingWindowChunker (M1-compat 512/64), StructureAware, factory, optional-адаптеры."""

from __future__ import annotations

import yaml

from graphrag_proto.ingestion_service.pipeline.chunker import (
    LangChainChunker,
    LlamaIndexChunker,
    SlidingWindowChunker,
    StructureAwareChunker,
    build_chunker,
    build_chunker_for,
)

LONG_TEXT = "слово " * 600  # 600 слов -> sliding window (512) даёт 2 чанка с шагом 448


def test_sliding_default_matches_m1_behavior() -> None:
    chunker = SlidingWindowChunker()
    assert chunker.chunk("short text") == ["short text"]
    chunks = chunker.chunk(LONG_TEXT)
    assert len(chunks) == 2
    assert all(len(c.split(" ")) <= 512 for c in chunks)
    # шаг = 512 - 64 = 448: первые слова второго чанка отстоят на 448
    assert chunks[1].split(" ")[0] == "слово"


def test_sliding_short_input_single_chunk() -> None:
    chunker = SlidingWindowChunker(chunk_size=10, overlap=2)
    assert chunker.chunk("a b c") == ["a b c"]
    assert chunker.chunk("   ") == []


def test_sliding_invalid_params_raise() -> None:
    import pytest

    with pytest.raises(ValueError):
        SlidingWindowChunker(chunk_size=0)
    with pytest.raises(ValueError):
        SlidingWindowChunker(overlap=-1)
    with pytest.raises(ValueError):
        SlidingWindowChunker(chunk_size=8, overlap=8)
    with pytest.raises(ValueError):
        SlidingWindowChunker(chunk_size=8, overlap=100)


def test_structure_aware_sections_stay_together() -> None:
    md = (
        "# Глава 1\nКраткий текст первой главы.\n"
        "# Глава 2\n" + ("казахское слово " * 600) + ".\n"
    )
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    chunks = chunker.chunk(md)
    # первая секция короткая -> один чанк с заголовком
    assert chunks[0] == "# Глава 1\nКраткий текст первой главы."
    # вторая секция длинная -> несколько чанков, заголовок в начале первого
    assert chunks[1].startswith("# Глава 2")
    assert len(chunks) >= 2


def test_structure_aware_single_short_section() -> None:
    chunker = StructureAwareChunker()
    assert chunker.chunk("просто текст") == ["просто текст"]
    assert chunker.chunk("  ") == []


def test_structure_aware_without_headers_falls_back_to_sliding() -> None:
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    chunks = chunker.chunk(LONG_TEXT)
    assert len(chunks) == 2
    assert chunks == SlidingWindowChunker().chunk(LONG_TEXT)


def test_structure_aware_short_document_single_chunk() -> None:
    md = "## Раздел А\nтело а\n\n## Раздел Б\nтело б\n"
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    # весь документ короче окна -> один чанк целиком (секции не рвутся)
    assert chunker.chunk(md) == [md]


def test_build_chunker_default_sliding(monkeypatch) -> None:
    monkeypatch.delenv("INGEST_CHUNKER", raising=False)
    monkeypatch.delenv("INGEST_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("INGEST_CHUNK_OVERLAP", raising=False)
    assert isinstance(build_chunker(), SlidingWindowChunker)


def test_build_chunker_env_strategy_and_sizes(monkeypatch) -> None:
    monkeypatch.setenv("INGEST_CHUNKER", "structure_aware")
    monkeypatch.setenv("INGEST_CHUNK_SIZE", "128")
    monkeypatch.setenv("INGEST_CHUNK_OVERLAP", "16")
    chunker = build_chunker()
    assert isinstance(chunker, StructureAwareChunker)
    assert all(len(c.split(" ")) <= 128 for c in chunker.chunk(LONG_TEXT))


def test_build_chunker_invalid_strategy_raises(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("INGEST_CHUNKER", "magic")
    with pytest.raises(ValueError):
        build_chunker()


def test_build_chunker_sliding_env_sizes(monkeypatch) -> None:
    monkeypatch.setenv("INGEST_CHUNKER", "sliding_window")
    monkeypatch.setenv("INGEST_CHUNK_SIZE", "10")
    monkeypatch.setenv("INGEST_CHUNK_OVERLAP", "2")
    chunker = build_chunker()
    assert isinstance(chunker, SlidingWindowChunker)
    assert chunker.chunk("a b c d e") == ["a b c d e"]


def test_langchain_chunker_fail_fast_without_package() -> None:
    import pytest

    with pytest.raises(RuntimeError):
        LangChainChunker().chunk("текст")


def test_langchain_chunker_invalid_splitter() -> None:
    import pytest

    with pytest.raises(ValueError):
        LangChainChunker(splitter="nope")


def test_llamaindex_chunker_fail_fast_without_package() -> None:
    import pytest

    with pytest.raises(RuntimeError):
        LlamaIndexChunker().chunk("текст")


def test_llamaindex_chunker_invalid_parser() -> None:
    import pytest

    with pytest.raises(ValueError):
        LlamaIndexChunker(parser="nope")


# ------------------------------------------------------------------ вариант 3:
# per-field precedence env > профиль домена > namespaces.yaml > дефолты M1


def _clear_env(monkeypatch) -> None:
    for key in (
        "INGEST_CHUNKER",
        "INGEST_CHUNK_SIZE",
        "INGEST_CHUNK_OVERLAP",
        "CONFIG_URL",
        "NAMESPACES_PATH",
    ):
        monkeypatch.delenv(key, raising=False)


def _write_namespaces(tmp_path, chunking: dict) -> None:
    path = tmp_path / "infra" / "config" / "namespaces.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"chunking": chunking}), encoding="utf-8")


def _patch_profile(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.pipeline.chunker._fetch_profile",
        lambda domain, config_url: payload,
    )


def test_precedence_env_over_profile(tmp_path, monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("INGEST_CHUNKER", "sliding_window")
    _write_namespaces(tmp_path, {"strategy": "structure_aware", "chunk_size": 128, "overlap": 16})
    _patch_profile(
        monkeypatch,
        {"chunking": {"strategy": "structure_aware", "chunk_size": 64, "overlap": 8}},
    )
    chunker = build_chunker_for("it", config_url="http://config-service:8001")
    assert isinstance(chunker, SlidingWindowChunker)


def test_precedence_profile_over_namespaces(tmp_path, monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NAMESPACES_PATH", str(tmp_path / "infra" / "config" / "namespaces.yaml"))
    _write_namespaces(tmp_path, {"strategy": "sliding_window", "chunk_size": 512, "overlap": 64})
    _patch_profile(
        monkeypatch,
        {"chunking": {"strategy": "structure_aware", "chunk_size": 96, "overlap": 12}},
    )
    chunker = build_chunker_for("it", config_url="http://config-service:8001")
    assert isinstance(chunker, StructureAwareChunker)
    # размер окна применён из профиля
    assert all(len(c.split(" ")) <= 96 for c in chunker.chunk(LONG_TEXT))


def test_precedence_namespaces_over_defaults(tmp_path, monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NAMESPACES_PATH", str(tmp_path / "infra" / "config" / "namespaces.yaml"))
    _write_namespaces(tmp_path, {"strategy": "structure_aware", "chunk_size": 128, "overlap": 16})
    chunker = build_chunker_for("it", config_url="")
    assert isinstance(chunker, StructureAwareChunker)


def test_precedence_defaults_when_nothing_set(tmp_path, monkeypatch) -> None:
    _clear_env(monkeypatch)
    chunker = build_chunker_for("it", config_url="")
    assert isinstance(chunker, SlidingWindowChunker)
    assert chunker._chunk_size == 512
    assert chunker._overlap == 64


def test_profile_fetch_failure_falls_back_to_defaults(tmp_path, monkeypatch) -> None:
    _clear_env(monkeypatch)
    _write_namespaces(tmp_path, {})
    monkeypatch.setenv("NAMESPACES_PATH", str(tmp_path / "infra" / "config" / "namespaces.yaml"))
    # Config Service недоступен -> _fetch_profile вернёт {} (исключение поглощено)
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.pipeline.chunker._fetch_profile",
        lambda domain, config_url: {},
    )
    chunker = build_chunker_for("it", config_url="http://config-service:8001")
    assert isinstance(chunker, SlidingWindowChunker)