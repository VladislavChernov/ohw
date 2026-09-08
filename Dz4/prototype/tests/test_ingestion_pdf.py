from __future__ import annotations

from pathlib import Path

from graphrag_proto.ingestion_service.readers.pdf import PdfReader
from tests.helpers import make_pdf


def test_pdf_reader_extracts_text(tmp_path: Path) -> None:
    p = tmp_path / "book.pdf"
    p.write_bytes(make_pdf("Война и мир глава первая"))
    doc = PdfReader().read(p, "src://book.pdf", "library")
    assert doc.doc_type == "pdf"
    assert any(b.type == "text" and "Война и мир" in b.data for b in doc.blocks)
    assert doc.content_hash.startswith("sha256:")


def test_pdf_reader_extracts_code_and_image_blocks(tmp_path: Path) -> None:
    p = tmp_path / "algo.pdf"
    p.write_bytes(make_pdf("def quicksort(a): return a", include_image=True))
    doc = PdfReader().read(p, "src://algo.pdf", "it")
    types = [b.type for b in doc.blocks]
    # весь текст страницы — кодовая сигнатура, поэтому code + image блоки
    assert any(t == "code" for t in types), types
    assert any(t == "image" for t in types), types


def test_pdf_reader_hyphen_joins_cyrillic(tmp_path: Path) -> None:
    p = tmp_path / "ru.pdf"
    # в реальном PDF перенос уже на стороне шрифта; проверяем нормализацию
    # через единый normalize_text на извлечённом поле (сост. кода).
    p.write_bytes(make_pdf("пере-\nнос"))
    doc = PdfReader().read(p, "src://ru.pdf", "library")
    assert doc.blocks
    assert all("пере-" not in b.data for b in doc.blocks if b.type == "text")


def test_pdf_sample_war_and_peace_extracts_cyrillic() -> None:
    """Сэмпл PDF (кириллица) из infra/samples извлекается читабельно."""
    prototype_root = Path(__file__).resolve().parents[1]
    sample = prototype_root / "infra" / "samples" / "library" / "war_and_peace.pdf"
    assert sample.exists(), sample
    doc = PdfReader().read(sample, "src://war_and_peace.pdf", "library")
    text = " ".join(b.data for b in doc.blocks if b.type == "text")
    assert "Война и мир" in text
    assert any(b.type == "image" for b in doc.blocks)