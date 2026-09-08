from __future__ import annotations

from pathlib import Path

from graphrag_proto.ingestion_service.document import (
    Block,
    Document,
    compute_content_hash,
    normalize_text,
)
from graphrag_proto.ingestion_service.readers.registry import MdReader, TxtReader, factory


def test_normalize_text_joins_hyphen_breaks_cyrillic() -> None:
    src = "сложный пере-\nнос и алго-\nритм"
    assert "пере-" not in normalize_text(src)  # перенос склеен
    assert "пере\n" not in normalize_text(src)
    assert normalize_text(src) == "сложный перенос и алгоритм\n"


def test_normalize_text_keeps_list_hyphens() -> None:
    src = "- первый пункт\n- второй пункт"
    out = normalize_text(src)
    assert "- первый пункт" in out


def test_normalize_text_stable_whitespace() -> None:
    assert normalize_text("a   b\t\tc") == "a b c\n"


def test_content_hash_source_independent(tmp_path: Path) -> None:
    """Контент-эквивалентные .txt и .md дают один content_hash (ADR-021)."""
    content = "алгоритм quicksort и B-дерево\n"
    txt_path = tmp_path / "doc.txt"
    md_path = tmp_path / "doc.md"
    txt_path.write_text(content, encoding="utf-8")
    md_path.write_text(content, encoding="utf-8")
    txt = TxtReader().read(txt_path, "src://a", "it")
    md = MdReader().read(md_path, "src://a", "it")
    assert txt.content_hash == md.content_hash


def test_content_hash_stable_across_text() -> None:
    a = Block(type="text", page=1, order=0, data="big o notation")
    b = Block(type="text", page=1, order=0, data="big o notation")
    assert compute_content_hash("it", [a]) == compute_content_hash("it", [b])


def test_content_hash_differs_by_content() -> None:
    a = Block(type="text", page=1, order=0, data="alpha")
    b = Block(type="text", page=1, order=0, data="beta")
    assert compute_content_hash("it", [a]) != compute_content_hash("it", [b])


def test_block_type_validation() -> None:
    import pytest

    with pytest.raises(ValueError):
        Block(type="nope", page=1, order=0, data="x")


def test_txt_reader_single_text_block(tmp_path: Path) -> None:
    p = tmp_path / "doc.txt"
    p.write_text("Привет, мир\n", encoding="utf-8")
    doc = TxtReader().read(p, "src://doc.txt", "it")
    assert doc.doc_type == "txt"
    assert doc.domain == "it"
    assert doc.content_hash.startswith("sha256:")
    assert [b.type for b in doc.blocks] == ["text"]


def test_md_reader_splits_code_blocks(tmp_path: Path) -> None:
    p = tmp_path / "doc.md"
    p.write_text(
        "Описание алгоритма.\n\n```python\ndef quicksort(a):\n    return a\n```\n\nКонец.",
        encoding="utf-8",
    )
    doc = MdReader().read(p, "src://doc.md", "it")
    types = [b.type for b in doc.blocks]
    assert "code" in types
    assert "text" in types
    assert any("def quicksort" in b.data for b in doc.blocks if b.type == "code")


def test_registry_factory_known_and_unknown() -> None:
    assert factory("txt").doc_type == "txt"
    assert factory("md").doc_type == "md"
    import pytest

    with pytest.raises(KeyError):
        factory("json")


def test_document_serialization_ordering() -> None:
    doc = Document(
        source_id="s",
        source_url="u",
        domain="it",
        doc_type="txt",
        content_hash="h",
        blocks=[
            Block(type="text", page=1, order=1, data="b"),
            Block(type="text", page=1, order=0, data="a"),
        ],
    )
    canonical = doc.serialize_canonical()
    assert [b["order"] for b in canonical["blocks"]] == [0, 1]