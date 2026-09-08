"""PDF-ридер (pypdf): текст, code-блоки, изображения (без OCR).

ADR-021: текст с нормализацией переносов (в т.ч. кириллица), code-блоки — строки
с кодовыми сигнатурами (моноширинные листинги), изображения — блоки type:image
с data.ref (смысл в M1 не индексируется).
"""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader as PyPdfReader

from graphrag_proto.ingestion_service.document import (
    Block,
    Document,
    compute_content_hash,
    normalize_text,
)
from graphrag_proto.ingestion_service.readers.base import DocumentReader

# Кодовые сигнатуры листингов: строка похожа на код (а не прозу).
_CODE_SIGNATURE_RE = re.compile(
    r"^\s*(def |class |function |async |var |const |let |import |from |"
    r"public |private |return |if\s*\(|for\s*\(|while\s*\(|=>|->)"
)


def _split_code_lines(text: str) -> list[tuple[str, str]]:
    """Разбивает текст страницы на (type, chunk): 'text' | 'code'.

    Последовательные строки, похожие на код, группируются в один code-блок.
    """
    chunks: list[tuple[str, str]] = []
    current: list[str] = []
    current_type = "text"

    def flush() -> None:
        nonlocal current, current_type
        if current:
            chunks.append((current_type, "\n".join(current).strip()))
            current = []

    for line in text.splitlines():
        line_type = "code" if _CODE_SIGNATURE_RE.match(line) else "text"
        if line_type != current_type:
            flush()
            current_type = line_type
        current.append(line)
    flush()
    return [(t, normalize_text(c)) for t, c in chunks if c.strip()]


class PdfReader(DocumentReader):
    doc_type = "pdf"

    def read(self, source: Path, source_url: str, domain: str) -> Document:
        reader = PyPdfReader(str(source))
        blocks: list[Block] = []
        order = 0
        for page_no in range(len(reader.pages)):
            page = reader.pages[page_no]
            page_no_1 = page_no + 1
            try:
                raw = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - битая страница -> без текста
                raw = ""
            for block_type, chunk in _split_code_lines(normalize_text(raw)):
                blocks.append(Block(type=block_type, page=page_no_1, order=order, data=chunk))
                order += 1
            # изображения страницы
            xobjects = self._page_images(page)
            for ref in xobjects:
                blocks.append(
                    Block(
                        type="image",
                        page=page_no_1,
                        order=order,
                        data={"ref": ref, "page": page_no_1},
                    )
                )
                order += 1
        content_hash = compute_content_hash(domain, blocks)
        return Document(
            source_id=source_url,
            source_url=source_url,
            domain=domain,
            doc_type=self.doc_type,
            content_hash=content_hash,
            blocks=blocks,
        )

    @staticmethod
    def _page_images(page: object) -> list[str]:
        result: list[str] = []
        try:
            resources = page.get("/Resources", {})  # type: ignore[attr-defined]
            xobjects = resources["/XObject"].get_object() if "/XObject" in resources else {}
            if isinstance(xobjects, dict):
                for name in xobjects:
                    obj = xobjects[name].get_object()
                    if isinstance(obj, dict) and obj.get("/Subtype") == "/Image":
                        result.append(name.lstrip("/"))
        except Exception:  # noqa: BLE001 - неразборчивые ресурсы -> без картинок
            return []
        return result