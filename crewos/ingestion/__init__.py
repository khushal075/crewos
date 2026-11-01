"""Public ingestion API. Other modules should import from this package only."""

from crewos.ingestion.bootstrap import build_plain_text_preparation
from crewos.ingestion.contracts import DocumentInput, StoredDocumentVersion, TextChunk
from crewos.ingestion.ports import (
    DocumentParseError, DocumentParserInterface, DocumentNotFound,
    DocumentStoreInterface, DocumentVersionConflict,
)
from crewos.ingestion.service import DocumentPreparationService

__all__ = [
    "DocumentInput",
    "StoredDocumentVersion",
    "DocumentStoreInterface",
    "DocumentNotFound",
    "DocumentVersionConflict",
    "TextChunk",
    "DocumentParseError",
    "DocumentParserInterface",
    "DocumentPreparationService",
    "build_plain_text_preparation",
]
