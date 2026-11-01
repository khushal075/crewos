"""Single-host immutable document snapshots; each operation owns its connection."""

from contextlib import contextmanager
from math import isfinite
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID

from pydantic import ValidationError

from crewos.ingestion.contracts import StoredDocumentVersion
from crewos.ingestion.adapters.sqlite_document_jobs import SQLiteDocumentJobs
from crewos.ingestion.document_store import (
    DocumentNotFound,
    DocumentStorageCorruption,
    DocumentVersionConflict,
)


class SQLiteDocumentStore(SQLiteDocumentJobs):
    APPLICATION_ID = 0x43524453
    SCHEMA_VERSION = 2

    def __init__(
        self,
        path: str | Path,
        *,
        lock_timeout_seconds: float = 5,
        job_lease_seconds: float = 180,
        job_max_attempts: int = 3,
    ):
        if not str(path).strip() or str(path) == ":memory:":
            raise ValueError("A file-backed document database is required")
        if (
            isinstance(lock_timeout_seconds, bool)
            or not isinstance(lock_timeout_seconds, (int, float))
            or not isfinite(lock_timeout_seconds)
            or lock_timeout_seconds <= 0
        ):
            raise ValueError("Database lock timeout must be finite and positive")
        if (
            type(job_max_attempts) is not int
            or job_max_attempts < 1
            or isinstance(job_lease_seconds, bool)
            or not isinstance(job_lease_seconds, (int, float))
            or not isfinite(job_lease_seconds)
            or job_lease_seconds <= 0
        ):
            raise ValueError("Invalid document job limits")
        self.job_lease_seconds, self.job_max_attempts = job_lease_seconds, job_max_attempts
        self.path = Path(path)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self._connection() as db:
            # Serialize initialization as well as version appends.
            db.execute("BEGIN IMMEDIATE")
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            fresh = application_id == 0 and version == 0 and not tables
            if not fresh and (
                application_id != self.APPLICATION_ID or version not in (1, self.SCHEMA_VERSION)
            ):
                raise ValueError("Unsupported or unrelated document database")
            db.execute(
                """CREATE TABLE IF NOT EXISTS document_versions (
                tenant_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version >= 1),
                snapshot TEXT NOT NULL,
                PRIMARY KEY(tenant_id, document_id, version)
                )"""
            )
            self._initialize_jobs(db)
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
            record = StoredDocumentVersion.model_validate_json(row["snapshot"])
        except (ValidationError, TypeError):
            raise DocumentStorageCorruption("Stored document snapshot is invalid") from None
        if (
            record.tenant_id != row["tenant_id"]
            or str(record.document_id) != row["document_id"]
            or record.version != row["version"]
        ):
            raise DocumentStorageCorruption("Stored document identity is inconsistent")
        return record

    def create_version(self, record: StoredDocumentVersion) -> StoredDocumentVersion:
        # Round-trip deeply: frozen models can still be bypassed with model_copy
        # or model_construct; nested existing instances must not skip validation.
        record = StoredDocumentVersion.model_validate_json(record.model_dump_json())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._append(db, record)
        return record

    def _append(self, db, record):
        identity = self._identity(record.tenant_id, record.document_id)
        latest = db.execute(
            "SELECT MAX(version) FROM document_versions WHERE tenant_id=? AND document_id=?",
            identity,
        ).fetchone()[0]
        expected = (latest or 0) + 1
        if record.version != expected:
            raise DocumentVersionConflict("Document version is not the next revision")
        db.execute(
            "INSERT INTO document_versions(tenant_id,document_id,version,snapshot) VALUES(?,?,?,?)",
            (*identity, record.version, record.model_dump_json()),
        )

    def get(
        self, *, tenant_id: str, document_id: UUID, version: int | None = None
    ) -> StoredDocumentVersion:
        identity = self._identity(tenant_id, document_id)
        if version is not None and (type(version) is not int or not 1 <= version <= 2**63 - 1):
            raise ValueError("Version must be a positive SQLite integer")
        with self._connection() as db:
            if version is None:
                row = db.execute(
                    "SELECT * FROM document_versions WHERE tenant_id=? AND document_id=? ORDER BY version DESC LIMIT 1",
                    identity,
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM document_versions WHERE tenant_id=? AND document_id=? AND version=?",
                    (*identity, version),
                ).fetchone()
            if row is None:
                raise DocumentNotFound("Document unavailable")
            return self._decode(row)

    def delete(self, *, tenant_id: str, document_id: UUID) -> None:
        identity = self._identity(tenant_id, document_id)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            deleted = db.execute(
                "DELETE FROM document_versions WHERE tenant_id=? AND document_id=?", identity
            ).rowcount
            if not deleted:
                raise DocumentNotFound("Document unavailable")
            # Fence in-flight updates and remove retained source payloads. Completed
            # receipts remain so the same idempotency key cannot recreate data.
            db.execute(
                """UPDATE document_jobs SET payload=NULL,
                state=CASE WHEN state='succeeded' THEN state ELSE 'failed' END,
                error=CASE WHEN state='succeeded' THEN error ELSE 'DocumentDeleted' END,
                token=NULL,lease_until=NULL WHERE tenant=? AND document_id=?""",
                identity,
            )
