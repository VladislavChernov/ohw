"""DocumentReader — контракт «источник → канонический документ» (ADR-021)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from graphrag_proto.ingestion_service.document import Document


class DocumentReader(ABC):
    """Единственный контракт чтения источника.

    Новый ридер (OCR, новые форматы) — новая реализация этого интерфейса,
    этапы пайплайна при этом не меняются (ADR-021).
    """

    doc_type: str

    @abstractmethod
    def read(self, source: Path, source_url: str, domain: str) -> Document:
        """Прочитать источник и вернуть канонический документ."""
        raise NotImplementedError