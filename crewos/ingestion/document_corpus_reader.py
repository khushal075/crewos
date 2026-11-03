"""Bounded, tenant-scoped current-document reads for downstream modules."""

from typing import Protocol
from crewos.ingestion.contracts import StoredDocumentVersion


class DocumentCorpusLimitExceeded(RuntimeError):
    """The tenant corpus exceeds configured scan admission; no partial corpus returned."""


class DocumentCorpusReaderInterface(Protocol):
    def read_latest_documents(
        self, *, tenant_id: str, max_documents: int, max_snapshot_bytes: int
    ) -> tuple[StoredDocumentVersion, ...]:
        """Read validated latest snapshots consistently within one tenant.

        Enforce limits before loading source snapshots; exceedance must fail,
        not silently omit documents. Caller must authorize access first. Reads
        reflect a transaction snapshot, not revocation of already-returned data.
        """
        ...
