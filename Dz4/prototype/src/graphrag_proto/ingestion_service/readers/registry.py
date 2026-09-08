"""Реестр ридеров по doc_type и простые текст-ридеры (txt/md)."""

from __future__ import annotations

import re
from pathlib import Path

from graphrag_proto.ingestion_service.document import (
    Block,
    Document,
    compute_content_hash,
    normalize_text,
)
from graphrag_proto.ingestion_service.readers.base import DocumentReader

_CODE_FENCE_RE = re.compile(r"```" + r".*?" + r"```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


class TxtReader(DocumentReader):
    doc_type = "txt"

    def read(self, source: Path, source_url: str, domain: str) -> Document:
        text = source.read_text(encoding="utf-8", errors="replace")
        blocks = [Block(type="text", page=1, order=0, data=normalize_text(text))]
        return Document(
            source_id=source_url,
            source_url=source_url,
            domain=domain,
            doc_type=self.doc_type,
            content_hash=compute_content_hash(domain, blocks),
            blocks=blocks,
        )


class MdReader(DocumentReader):
    doc_type = "md"

    def read(self, source: Path, source_url: str, domain: str) -> Document:
        text = source.read_text(encoding="utf-8", errors="replace")
        blocks: list[Block] = []
        order = 0
        pos = 0
        for m in _CODE_FENCE_RE.finditer(text):
            if m.start() > pos:
                blocks.append(
                    Block(
                        type="text",
                        page=1,
                        order=order,
                        data=normalize_text(text[pos : m.start()]),
                    )
                )
                order += 1
            blocks.append(Block(type="code", page=1, order=order, data=normalize_text(m.group())))
            order += 1
            pos = m.end()
        if pos < len(text):
            blocks.append(Block(type="text", page=1, order=order, data=normalize_text(text[pos:])))
        return Document(
            source_id=source_url,
            source_url=source_url,
            domain=domain,
            doc_type=self.doc_type,
            content_hash=compute_content_hash(domain, blocks),
            blocks=blocks,
        )


def factory(doc_type: str) -> DocumentReader:
    readers: dict[str, DocumentReader] = {
        "txt": TxtReader(),
        "md": MdReader(),
        "pdf": _lazy_pdf_reader(),
    }
    reader = readers.get(doc_type)
    if reader is None:
        raise KeyError(f"нет ридера для doc_type={doc_type!r}")
    return reader


def _lazy_pdf_reader() -> DocumentReader:
    from graphrag_proto.ingestion_service.readers.pdf import PdfReader

    return PdfReader()