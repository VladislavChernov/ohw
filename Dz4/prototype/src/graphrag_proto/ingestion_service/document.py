"""Canonical document model and source-independent content hashing.

Контракт ADR-021: пайплайн (CHUNK..COMMIT) работает ТОЛЬКО с этим представлением.
content_hash считается с нормализованного канонического вида, а не с байтов источника.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

BLOCK_TYPES = ("text", "code", "image")

# Склейка переносов слов вида "пере-\nнос" -> "перенос" (в т.ч. кириллица),
# но НЕ склейка регламентных переносов после "- " (маркеры списков).
_HYPHEN_JOIN_RE = re.compile(r"(?<=[\wа-яА-ЯёЁ])\-\s*\n\s*(?=[\wа-яА-ЯёЁ])")
_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Нормализация текста для content_hash (стабильна, источник-независима)."""
    text = _HYPHEN_JOIN_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip() + "\n"


@dataclass(frozen=True)
class Block:
    type: str
    page: int
    order: int
    data: Any

    def __post_init__(self) -> None:
        if self.type not in BLOCK_TYPES:
            raise ValueError(f"неизвестный тип блока: {self.type!r}")


@dataclass
class Document:
    source_id: str
    source_url: str
    domain: str
    doc_type: str
    content_hash: str
    blocks: list[Block] = field(default_factory=list)

    def serialize_canonical(self) -> dict[str, Any]:
        """Сериализация канонического вида.

        content_hash не зависит от источника и doc_type: контент-эквивалентные
        .txt/.md/.pdf дают один хэш (источник-агностичность, ADR-021).
        """
        return {
            "domain": self.domain,
            "blocks": [
                {"type": b.type, "page": b.page, "order": b.order, "data": b.data}
                for b in sorted(self.blocks, key=lambda x: (x.order, x.page))
            ],
        }


def compute_content_hash(domain: str, blocks: list[Block]) -> str:
    doc = Document(
        source_id="",
        source_url="",
        domain=domain,
        doc_type="",
        content_hash="",
        blocks=blocks,
    )
    canonical = json.dumps(
        doc.serialize_canonical(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()