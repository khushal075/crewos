"""
SQLite storage for source registration and safe retries.
"""
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from crewos_ingestion.contracts.context_contracts.caller_context import TenantId
from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.contracts.output_contracts.source_registration import SourceAcceptanceReceipt
from crewos_ingestion.storage.source_store import SourceRegistrationConflict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_versions (
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL, 
    version INTEGER NOT NULL CHECK (version >= 1), 
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_reference TEXT,
    PRIMARY KEY (tenant_id, source_id, version)
);

CREATE TABLE IF NOT EXISTS source_registrations (
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    source_id TEXT NOT NULL,
    version INTEGER NOT NULL, 
    PRIMARY KEY (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, source_id, version)
    REFERENCES source_versions (tenant_id, source_id, version)
);

"""

class SQLiteSourceStore:
    """
    Store source registrations in a file-backed SQLite database.
    """

    def __init__(self, database_path: str | Path) -> None:
        if str(database_path) in {"", ":memory:"}:
            raise ValueError("A SQLite database file path is required.")

        self._database_path = database_path
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def register(
            self,
            *,
            tenant_id: TenantId,
            submission: SourceSubmission,
            idempotency_key: str | None = None,
    ) -> SourceAcceptanceReceipt:
        """
        Create version 1 or return the receipt for an identical retry.

        :param tenant_id:
        :param idempotency_key:
        :return:
        """
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = self._find_registration(
                    db,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key
                )
                if existing is not None:
                    return self._receipt_for_retry(existing, submission)

            receipt = SourceAcceptanceReceipt(source_id=uuid4(), version=1)
            db.execute(
                """
                INSERT INTO source_versions (
                    tenant_id, source_id, version, title, content, source_reference
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    str(receipt.source_id),
                    receipt.version,
                    submission.title,
                    submission.content,
                    submission.source_reference
                ),
            )
            if idempotency_key is not None:
                db.execute(
                    """
                    INSERT INTO source_registrations (
                        tenant_id, idempotency_key, source_id, version
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        idempotency_key,
                        str(receipt.source_id),
                        receipt.version
                    ),
                )
        return receipt


    def _find_registration(
            self,
            db: sqlite3.Connection,
            *,
            tenant_id: TenantId,
            idempotency_key: str,
    )-> sqlite3.Row | None:

        return db.execute(
            """
            SELECT 
                s.source_id, 
                s.version, 
                s.title, 
                s.content, 
                s.source_reference
            FROM
                source_registrations AS r 
            JOIN
                source_versions AS s 
            ON
                s.tenant_id = r.tenant_id 
            AND
                s.source_id = r.source_id
            AND 
                s.version == r.version
            WHERE
                r.tenant_id = ? AND r.idempotency_key = ?
            """,
            (tenant_id, idempotency_key),
        ).fetchone()


    def _receipt_for_retry(
            self,
            existing: sqlite3.Row,
            submission: SourceSubmission
    ) -> SourceAcceptanceReceipt:
        if (
            existing["title"] != submission.title
            or existing["content"] != submission.content
            or existing["source_reference"] != submission.source_reference
        ):
            raise SourceRegistrationConflict(
                "This idempotency key was already used for a different submission."
            )

        return SourceAcceptanceReceipt(
            source_id=existing["source_id"],
            version=existing["version"],
        )