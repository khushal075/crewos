"""Durable submission contracts; public job receipts never contain source content."""

from dataclasses import dataclass
from typing import Literal, Protocol
from pydantic import BaseModel, ConfigDict, UUID4, Field

from crewos.chunking import ChunkingOptions
from crewos.ingestion.document_store import DocumentStoreInterface
from crewos.ingestion.contracts import (
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    StoredDocumentVersion,
)


class DocumentJobConflict(RuntimeError):
    pass


class DocumentJobNotFound(LookupError):
    pass


class DocumentJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID4
    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    document_id: UUID4
    operation: Literal["create", "update"]
    state: Literal["queued", "running", "succeeded", "failed"]
    version: int | None = Field(default=None, strict=True, ge=1)
    attempts: int = Field(strict=True, ge=0)
    error_code: str | None = None
    publication_confirmed: bool
    lease_active: bool
    result_available: bool


class DocumentSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job: DocumentJob
    replayed: bool


@dataclass(frozen=True)
class DocumentJobClaim:
    job: DocumentJob
    request: DocumentIngestionRequest | DocumentUpdateRequest
    options: ChunkingOptions
    token: str


class DocumentJobStoreInterface(Protocol):
    def submit_job(
        self,
        *,
        tenant_id: str,
        request: DocumentIngestionRequest | DocumentUpdateRequest,
        operation: Literal["create", "update"],
        options: ChunkingOptions,
        idempotency_key: str,
    ) -> DocumentSubmission: ...
    def get_job(self, job_id: str, tenant_id: str) -> DocumentJob: ...
    def claim_job(self, job_id: str, tenant_id: str) -> DocumentJobClaim | None: ...
    def commit_job(
        self, claim: DocumentJobClaim, record: StoredDocumentVersion
    ) -> StoredDocumentVersion:
        """Atomically fence the claim, append the snapshot, and record success."""
        ...

    def fail_job(self, claim: DocumentJobClaim, error_code: str) -> None: ...
    def retry_job(self, job_id: str, tenant_id: str) -> DocumentJob: ...
    def mark_job_published(self, job_id: str, tenant_id: str) -> None: ...


class DocumentManagementStoreInterface(DocumentStoreInterface, DocumentJobStoreInterface, Protocol):
    """Combined persistence boundary; snapshots and job outcomes share a transaction."""
