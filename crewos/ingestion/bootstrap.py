"""Composition root for document preparation and single-host storage."""

from pathlib import Path

from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions, build_character_chunking
from crewos.ingestion.document_ingestion_facade import DocumentIngestionFacade
from crewos.ingestion.document_processing_coordinator import DocumentProcessingCoordinator

from crewos.ingestion.adapters.plain_text import PlainTextDocumentParser
from crewos.ingestion.adapters.sqlite_document_store import SQLiteDocumentStore
from crewos.ingestion.document_store import DocumentStoreInterface
from crewos.ingestion.document_jobs import DocumentManagementStoreInterface
from crewos.ingestion.service import DocumentPreparationService


def build_plain_text_preparation(
    *, max_source_bytes: int = 4_000_000
) -> DocumentPreparationService:
    return DocumentPreparationService(
        parser=PlainTextDocumentParser(max_source_bytes=max_source_bytes)
    )


def build_sqlite_document_store(
    path: str | Path,
    *,
    lock_timeout_seconds: float = 5,
    job_lease_seconds: float = 180,
    job_max_attempts: int = 3,
) -> DocumentManagementStoreInterface:
    """Use a dedicated local file; no database is opened during package import."""
    return SQLiteDocumentStore(
        path,
        lock_timeout_seconds=lock_timeout_seconds,
        job_lease_seconds=job_lease_seconds,
        job_max_attempts=job_max_attempts,
    )


def build_plain_text_ingestion_facade(
    *,
    store: DocumentStoreInterface,
    executor: PolicyEnforcedExecutor,
    chunking_options: ChunkingOptions | None = None,
) -> DocumentIngestionFacade:
    """Wire public authorization separately from internal processing."""
    coordinator = DocumentProcessingCoordinator(
        preparation=build_plain_text_preparation(),
        chunking=build_character_chunking(),
        store=store,
        chunking_options=chunking_options,
    )
    return DocumentIngestionFacade(coordinator=coordinator, executor=executor)
