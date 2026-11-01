"""Composition root: construct the currently supported plain-text preparation path."""

from crewos.ingestion.adapters.plain_text import PlainTextDocumentParser
from crewos.ingestion.service import DocumentPreparationService


def build_plain_text_preparation(
    *, max_source_bytes: int = 4_000_000
) -> DocumentPreparationService:
    return DocumentPreparationService(
        parser=PlainTextDocumentParser(max_source_bytes=max_source_bytes)
    )
