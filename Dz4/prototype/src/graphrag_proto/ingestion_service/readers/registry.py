"""Реестр ридеров по doc_type и простые текст-ридеры (txt/md)."""

from __future__ import annotations

import logging
import os
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

#: Разделять ли markdown на блоки `text`/`code` по ограждениям. По умолчанию НЕТ -
#: см. `MdReader`. Значение «1» возвращает прежнее поведение для A/B-сравнения.
CODE_SPLIT_ENV = "MD_KEEP_CODE_SPLIT"

_log = logging.getLogger("graphrag_proto.ingestion.readers")


def _keep_code_split() -> bool:
    """Читает флаг раздельного чтения markdown.

    Нераспознанное значение НЕ трактуется молча как «не режем»: молчание здесь опаснее
    отказа, потому что переменная меняет `content_hash` всего корпуса, и нераспознанное
    «да» тихо вернуло бы старое разбиение вместе с чужими границами чанков.
    """
    raw = (os.environ.get(CODE_SPLIT_ENV) or "").strip()
    if not raw:
        return False
    value = raw.lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    _log.warning(
        "%s=%r не распознано; ожидается 1/0. Трактуется как «не режем» (ADR-054), "
        "но разбиение при этом меняет content_hash всего корпуса.",
        CODE_SPLIT_ENV,
        raw,
    )
    return False


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
    """Markdown-документ.

    По умолчанию НЕ режет файл по ограждениям кода: весь документ приходит одним блоком
    `text`, и ограждения ``` остаются в тексте. Причина измерением: разрез по блокам
    оставлял подводку в одном чанке, а её содержимое - в соседнем. Из десяти чанков без
    кандидатов на корпусе все десять были такими подводками или заголовками, у которых
    стоило содержание уехало в блок кода.

    Раздельное чтение возвращено переменной `MD_KEEP_CODE_SPLIT` - не как запасной путь,
    а чтобы A/B-сравнение было возможно: правка меняет границы чанков, а значит
    индексацию и все базлайны прибора.
    """

    doc_type = "md"

    def read(self, source: Path, source_url: str, domain: str) -> Document:
        text = source.read_text(encoding="utf-8", errors="replace")
        blocks: list[Block] = []
        order = 0
        pos = 0
        if _keep_code_split():
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
                blocks.append(
                    Block(type="code", page=1, order=order, data=normalize_text(m.group()))
                )
                order += 1
                pos = m.end()
            if pos < len(text):
                blocks.append(
                    Block(type="text", page=1, order=order, data=normalize_text(text[pos:]))
                )
        else:
            blocks.append(Block(type="text", page=1, order=order, data=normalize_text(text)))
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