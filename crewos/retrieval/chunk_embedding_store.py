"""Tenant-scoped derived-vector persistence boundary; no adapter is implemented yet."""

from typing import Protocol
from uuid import UUID

from crewos.retrieval.embedding_storage_contracts import (
    ChunkEmbeddingWriteBatch,
    EmbeddingSpace,
    StoredChunkEmbedding,
)


class EmbeddingWriteConflict(RuntimeError):
    """A source/configuration key already has different persisted content."""


class EmbeddingStorageCorruption(RuntimeError):
    """Persisted vectors or indexed identities violate the contract."""


class EmbeddingStorageLimitExceeded(RuntimeError):
    """Read admission exceeded; no truncated or partial collection is returned."""


class ChunkEmbeddingStoreInterface(Protocol):
    """Call only from authorized application code; every adapter must enforce scope.

    Records are derived cache/index data. They do not establish that a source is
    current, available or still authorized. Before ranking/returning passages,
    retrieval must check the authoritative current tenant document snapshot.
    """

    def put(self, batch: ChunkEmbeddingWriteBatch) -> None:
        """Revalidate and atomically save a bounded single-tenant batch.

        Key: tenant, space.key, document ID, document version and chunk index.
        Identical replays are no-ops. Different content under an existing key
        raises EmbeddingWriteConflict without changing any record in the batch.
        Do not overwrite immutable records or hide partial writes. A document may
        require several batches; this does not publish an entire index atomically.
        """
        ...

    def read_document(
        self,
        *,
        tenant_id: str,
        document_id: UUID,
        version: int,
        space: EmbeddingSpace,
        max_records: int,
        max_snapshot_bytes: int,
    ) -> tuple[StoredChunkEmbedding, ...]:
        """Read an exact version/configuration, ordered by chunk index.

        Validate scope and positive admission limits. Apply tenant/version/space
        filters and count/byte admission before loading records in one consistent
        snapshot. Missing or other-tenant matches return (). Corruption and
        infrastructure failures must raise. Incomplete cached documents are
        possible; callers must check coverage against the authoritative source.
        """
        ...

    def delete_document(self, *, tenant_id: str, document_id: UUID) -> None:
        """Idempotently delete this tenant's vectors for all versions and spaces.

        Other tenants are untouched. This does not delete source content or fence
        concurrent stale index writers; lifecycle coordination remains separate.
        """
        ...
