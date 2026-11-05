"""Single-host immutable derived vectors; source lifecycle remains ingestion-owned."""

from contextlib import contextmanager
from math import isfinite
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID

from pydantic import ValidationError

from crewos.retrieval.chunk_embedding_store import (
    EmbeddingStorageCorruption,
    EmbeddingStorageLimitExceeded,
    EmbeddingWriteConflict,
)
from crewos.retrieval.embedding_storage_contracts import (
    ChunkEmbeddingWriteBatch,
    EmbeddingSpace,
    StoredChunkEmbedding,
)


class SQLiteChunkEmbeddingStore:
    APPLICATION_ID = 0x43524556
    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path, *, lock_timeout_seconds: float = 2):
        if not str(path).strip() or str(path) == ":memory:":
            raise ValueError("A file-backed embedding database is required")
        if (
            type(lock_timeout_seconds) not in (int, float)
            or not isfinite(lock_timeout_seconds)
            or not 0 < lock_timeout_seconds <= 30
        ):
            raise ValueError("Embedding lock timeout must be finite and in (0, 30]")
        self.path = Path(path)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            fresh = app_id == 0 and version == 0 and not tables
            if not fresh and (app_id != self.APPLICATION_ID or version != self.SCHEMA_VERSION):
                raise ValueError("Unsupported or unrelated embedding database")
            db.execute(
                """CREATE TABLE IF NOT EXISTS chunk_embeddings (
                tenant_id TEXT NOT NULL, space_key TEXT NOT NULL,
                document_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1),
                chunk_index INTEGER NOT NULL CHECK(chunk_index>=0), snapshot TEXT NOT NULL,
                PRIMARY KEY(tenant_id, space_key, document_id, version, chunk_index)
            )"""
            )
            db.execute(f"PRAGMA application_id={self.APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=self.lock_timeout_seconds)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _identity(tenant_id, document_id):
        if (
            not isinstance(tenant_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id) is None
        ):
            raise ValueError("Invalid tenant identity")
        if not isinstance(document_id, UUID):
            raise TypeError("Document identity must be a UUID")
        return tenant_id, str(document_id)

    @staticmethod
    def _decode(row):
        try:
            record = StoredChunkEmbedding.model_validate_json(row["snapshot"])
        except (ValidationError, TypeError):
            raise EmbeddingStorageCorruption("Invalid stored embedding snapshot") from None
        if (
            record.tenant_id != row["tenant_id"]
            or record.space.key != row["space_key"]
            or str(record.passage.document_id) != row["document_id"]
            or record.passage.document_version != row["version"]
            or record.passage.chunk.index != row["chunk_index"]
        ):
            raise EmbeddingStorageCorruption("Embedding identity differs from indexed identity")
        return record

    def put(self, batch: ChunkEmbeddingWriteBatch) -> None:
        batch = ChunkEmbeddingWriteBatch.model_validate_json(batch.model_dump_json())
        snapshots = [record.model_dump_json() for record in batch.records]
        if sum(len(snapshot.encode("utf-8")) for snapshot in snapshots) > 32_000_000:
            raise EmbeddingStorageLimitExceeded("Embedding write exceeds 32 MB admission")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for record, snapshot in zip(batch.records, snapshots):
                key = (
                    record.tenant_id,
                    record.space.key,
                    str(record.passage.document_id),
                    record.passage.document_version,
                    record.passage.chunk.index,
                )
                row = db.execute(
                    "SELECT * FROM chunk_embeddings WHERE tenant_id=? AND space_key=? AND document_id=? AND version=? AND chunk_index=?",
                    key,
                ).fetchone()
                if row is not None:
                    if self._decode(row) != record:
                        raise EmbeddingWriteConflict(
                            "Embedding key already contains different data"
                        )
                else:
                    db.execute("INSERT INTO chunk_embeddings VALUES(?,?,?,?,?,?)", (*key, snapshot))

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
        tenant, identity = self._identity(tenant_id, document_id)
        if type(version) is not int or not 1 <= version <= 2**63 - 1:
            raise ValueError("Version must be a positive SQLite integer")
        if any(type(value) is not int or value < 1 for value in (max_records, max_snapshot_bytes)):
            raise ValueError("Read admission limits must be positive integers")
        space = EmbeddingSpace.model_validate_json(space.model_dump_json())
        key = (tenant, space.key, identity, version)
        where = "tenant_id=? AND space_key=? AND document_id=? AND version=?"
        with self._connection() as db:
            db.execute("BEGIN")
            count, size = db.execute(
                f"SELECT COUNT(*), COALESCE(SUM(length(CAST(snapshot AS BLOB))),0) FROM chunk_embeddings WHERE {where}",
                key,
            ).fetchone()
            if count > max_records or size > max_snapshot_bytes:
                raise EmbeddingStorageLimitExceeded("Embedding read exceeds admission")
            rows = db.execute(
                f"SELECT * FROM chunk_embeddings WHERE {where} ORDER BY chunk_index", key
            ).fetchall()
            return tuple(self._decode(row) for row in rows)

    def delete_document(self, *, tenant_id: str, document_id: UUID) -> None:
        key = self._identity(tenant_id, document_id)
        with self._connection() as db:
            db.execute("DELETE FROM chunk_embeddings WHERE tenant_id=? AND document_id=?", key)
