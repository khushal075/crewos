"""Tenant-scoped document storage interface and lifecycle errors."""

from typing import Protocol
from uuid import UUID

from crewos.ingestion.contracts import StoredDocumentVersion


class DocumentNotFound(LookupError):
    """Document/version unavailable, including requests outside its tenant."""


class DocumentVersionConflict(RuntimeError):
    """Version already exists or is not the next sequential version."""


class DocumentStorageCorruption(RuntimeError):
    """Persisted snapshot is invalid or does not match its indexed identity."""


class DocumentStoreInterface(Protocol):
    """Tenant-scoped immutable snapshot storage contract.

    Calls originate from authorized application code. Storage must additionally
    scope all operations by tenant. Database transactions must make version and
    chunk writes atomic. Adapters must preserve validated snapshots and propagate
    storage failures, never report partial writes as success.
    """

    def create_version(self, record: StoredDocumentVersion) -> StoredDocumentVersion:
        """Atomically append an immutable snapshot and return the saved record.

        For a (tenant_id, document_id), the first version is 1 and subsequent
        versions must be latest + 1. Concurrent or duplicate writes raise
        DocumentVersionConflict; existing versions must never be overwritten.
        Validate records again at the storage boundary before writing.
        """
        ...

    def get(
        self, *, tenant_id: str, document_id: UUID, version: int | None = None
    ) -> StoredDocumentVersion:
        """Read an exact version, or the latest when version is None.

        Missing and other-tenant records both raise DocumentNotFound. Returned
        snapshots must be validated; corruption is a storage failure.
        """
        ...

    def delete(self, *, tenant_id: str, document_id: UUID) -> None:
        """Atomically delete all stored versions/chunks in this tenant scope.

        Missing and other-tenant records raise DocumentNotFound. This operation
        does not imply deletion from future external search indexes or backups;
        their propagation and retention require separate lifecycle handling.
        """
        ...
