"""Public ingestion API. Other modules should import from this package only."""

from crewos.ingestion.bootstrap import build_plain_text_preparation, build_sqlite_document_store
from crewos.ingestion.contracts import DocumentInput, StoredDocumentVersion, TextChunk
from crewos.ingestion.document_parser import DocumentParseError, DocumentParserInterface
from crewos.ingestion.document_store import (
    DocumentNotFound,
    DocumentStoreInterface,
    DocumentStorageCorruption,
    DocumentVersionConflict,
)
from crewos.ingestion.service import DocumentPreparationService

__all__ = [
    "DocumentInput",
    "StoredDocumentVersion",
    "DocumentStoreInterface",
    "DocumentStorageCorruption",
    "build_sqlite_document_store",
    "DocumentNotFound",
    "DocumentVersionConflict",
    "TextChunk",
    "DocumentParseError",
    "DocumentParserInterface",
    "DocumentPreparationService",
    "build_plain_text_preparation",
]
