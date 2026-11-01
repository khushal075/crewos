"""Public ingestion API. Other modules should import from this package only."""

from crewos.ingestion.bootstrap import (
    build_plain_text_preparation,
    build_sqlite_document_store,
    build_plain_text_ingestion_facade,
)
from crewos.ingestion.contracts import (
    DocumentInput,
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    DocumentReadRequest,
    DocumentDeleteRequest,
    DocumentDeleted,
    StoredDocumentVersion,
    TextChunk,
)
from crewos.ingestion.document_parser import DocumentParseError, DocumentParserInterface
from crewos.ingestion.document_store import (
    DocumentNotFound,
    DocumentStoreInterface,
    DocumentStorageCorruption,
    DocumentVersionConflict,
)
from crewos.ingestion.service import DocumentPreparationService
from crewos.ingestion.document_ingestion_facade import DocumentIngestionFacade

__all__ = [
    "DocumentReadRequest",
    "DocumentDeleteRequest",
    "DocumentDeleted",
    "DocumentJob",
    "DocumentSubmission",
    "DocumentJobConflict",
    "DocumentJobNotFound",
    "DocumentJobStoreInterface",
    "DocumentManagementStoreInterface",
    "DocumentSubmissionFacade",
    "process_document_job",
    "DocumentInput",
    "DocumentIngestionRequest",
    "DocumentUpdateRequest",
    "DocumentIngestionFacade",
    "build_plain_text_ingestion_facade",
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

from crewos.ingestion.document_jobs import (
    DocumentJob,
    DocumentSubmission,
    DocumentJobConflict,
    DocumentJobNotFound,
    DocumentJobStoreInterface,
    DocumentManagementStoreInterface,
)
from crewos.ingestion.document_submission_facade import DocumentSubmissionFacade
from crewos.ingestion.document_job_processor import process_document_job
