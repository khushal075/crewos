"""Transactional source events and tenant-scoped leased cleanup delivery."""

from datetime import datetime, timezone
from math import isfinite
import logging
import re
from time import time
from uuid import UUID, uuid4

from pydantic import ValidationError

from crewos.ingestion.document_cleanup_contracts import (
    CleanupFailureCode,
    DocumentCleanupClaim,
    DocumentDeletedEvent,
    DocumentVersionCreatedEvent,
    parse_cleanup_event_json,
)
from crewos.ingestion.document_cleanup_outbox import CleanupLeaseLost, CleanupOutboxCorruption

logger = logging.getLogger(__name__)


class SQLiteDocumentCleanupOutbox:
    def _initialize_cleanup_outbox(self, db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(document_cleanup_outbox)")}
        legacy = bool(columns) and "event_type" not in columns
        if legacy:
            db.execute(
                "ALTER TABLE document_cleanup_outbox RENAME TO document_cleanup_outbox_legacy"
            )
        db.execute(
            """CREATE TABLE IF NOT EXISTS document_cleanup_outbox (
            event_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, document_id TEXT NOT NULL,
            event_payload TEXT NOT NULL, occurred_at REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','processing','completed')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 2147483647),
            lease_token TEXT, lease_until REAL, next_attempt_at REAL NOT NULL,
            last_error_code TEXT CHECK(last_error_code IS NULL OR last_error_code IN ('storage_unavailable','cleanup_failed')),
            completed_at REAL,
            event_type TEXT NOT NULL CHECK(event_type IN ('document.deleted','document.version_created')),
            source_version INTEGER NOT NULL CHECK(
                (event_type='document.deleted' AND source_version=0) OR
                (event_type='document.version_created' AND source_version>=2)),
            UNIQUE(tenant_id,document_id,event_type,source_version)
        )"""
        )
        if legacy:
            db.execute(
                """INSERT INTO document_cleanup_outbox
                SELECT event_id,tenant_id,document_id,event_payload,occurred_at,state,attempts,
                       lease_token,lease_until,next_attempt_at,last_error_code,completed_at,
                       'document.deleted',0 FROM document_cleanup_outbox_legacy"""
            )
            db.execute("DROP TABLE document_cleanup_outbox_legacy")
        db.execute(
            """CREATE INDEX IF NOT EXISTS document_cleanup_pending
            ON document_cleanup_outbox(tenant_id,state,next_attempt_at,occurred_at,event_id)"""
        )

    def _record_cleanup_event(self, db, event):
        if not db.in_transaction:
            raise RuntimeError("Deletion-event recording requires an active source transaction")
        db.execute(
            """INSERT INTO document_cleanup_outbox
            (event_id,tenant_id,document_id,event_payload,occurred_at,next_attempt_at,event_type,source_version)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                str(event.event_id),
                event.tenant_id,
                str(event.document_id),
                event.model_dump_json(),
                event.occurred_at.timestamp(),
                event.occurred_at.timestamp(),
                event.event_type,
                event.document_version if isinstance(event, DocumentVersionCreatedEvent) else 0,
            ),
        )

    def _record_document_deleted(self, db, *, tenant_id, document_id):
        self._record_cleanup_event(
            db,
            DocumentDeletedEvent(
                event_id=uuid4(),
                tenant_id=tenant_id,
                document_id=document_id,
                occurred_at=datetime.now(timezone.utc),
            ),
        )

    def _record_document_version(self, db, *, tenant_id, document_id, version):
        self._record_cleanup_event(
            db,
            DocumentVersionCreatedEvent(
                event_id=uuid4(),
                tenant_id=tenant_id,
                document_id=document_id,
                document_version=version,
                occurred_at=datetime.now(timezone.utc),
            ),
        )

    def _backfill_version_cleanup(self, db):
        # Existing current versions establish safe monotonic floors, without reading text.
        rows = db.execute(
            """SELECT tenant_id,document_id,MAX(version) AS version
            FROM document_versions GROUP BY tenant_id,document_id HAVING MAX(version)>1"""
        ).fetchall()
        for row in rows:
            existing = db.execute(
                """SELECT 1 FROM document_cleanup_outbox
                WHERE tenant_id=? AND document_id=? AND event_type='document.version_created' AND source_version=?""",
                (row["tenant_id"], row["document_id"], row["version"]),
            ).fetchone()
            if existing is None:
                self._record_document_version(
                    db,
                    tenant_id=row["tenant_id"],
                    document_id=UUID(row["document_id"]),
                    version=row["version"],
                )

    @staticmethod
    def _cleanup_tenant(tenant_id):
        if (
            not isinstance(tenant_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id) is None
        ):
            raise ValueError("Invalid cleanup tenant identity")
        return tenant_id

    @staticmethod
    def _cleanup_duration(value, *, maximum, allow_zero=False):
        if (
            type(value) not in (int, float)
            or value > maximum
            or value < 0
            or not isfinite(value)
            or (not allow_zero and value == 0)
        ):
            raise ValueError("Invalid cleanup lease or retry duration")
        return value

    @staticmethod
    def _cleanup_uuid(value):
        if not isinstance(value, UUID) or value.version != 4:
            raise ValueError("Cleanup identity must be a version-4 UUID")
        return str(value)

    @staticmethod
    def _decode_cleanup_row(row):
        try:
            if (
                not isinstance(row["event_payload"], str)
                or len(row["event_payload"].encode("utf-8")) > 16384
            ):
                raise ValueError("Oversized event")
            event = parse_cleanup_event_json(row["event_payload"])
            if (
                str(event.event_id) != row["event_id"]
                or event.tenant_id != row["tenant_id"]
                or str(event.document_id) != row["document_id"]
                or event.occurred_at.timestamp() != row["occurred_at"]
                or event.event_type != row["event_type"]
                or (event.document_version if isinstance(event, DocumentVersionCreatedEvent) else 0)
                != row["source_version"]
            ):
                raise ValueError("Event identity mismatch")
            if (
                type(row["attempts"]) is not int
                or not 0 <= row["attempts"] <= 2**31 - 1
                or type(row["next_attempt_at"]) not in (int, float)
                or not isfinite(row["next_attempt_at"])
            ):
                raise ValueError("Invalid delivery metadata")
            if row["last_error_code"] is not None:
                CleanupFailureCode(row["last_error_code"])
            state = row["state"]
            if state == "pending":
                if (
                    row["lease_token"] is not None
                    or row["lease_until"] is not None
                    or row["completed_at"] is not None
                ):
                    raise ValueError("Pending event has ownership")
            elif state in {"processing", "completed"}:
                token = UUID(row["lease_token"])
                if token.version != 4 or str(token) != row["lease_token"] or row["attempts"] < 1:
                    raise ValueError("Invalid ownership identity")
                if state == "processing":
                    if (
                        type(row["lease_until"]) not in (int, float)
                        or not isfinite(row["lease_until"])
                        or row["lease_until"] <= event.occurred_at.timestamp()
                        or row["completed_at"] is not None
                    ):
                        raise ValueError("Invalid active lease")
                elif (
                    row["lease_until"] is not None
                    or type(row["completed_at"]) not in (int, float)
                    or not isfinite(row["completed_at"])
                    or row["completed_at"] < event.occurred_at.timestamp()
                ):
                    raise ValueError("Invalid completion metadata")
            else:
                raise ValueError("Unknown delivery state")
        except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
            raise CleanupOutboxCorruption("Invalid cleanup event or delivery identity") from None
        return event

    def claim_next(self, *, tenant_id, lease_seconds):
        tenant_id = self._cleanup_tenant(tenant_id)
        lease_seconds = self._cleanup_duration(lease_seconds, maximum=300)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time()
            row = db.execute(
                """SELECT * FROM document_cleanup_outbox
                WHERE tenant_id=? AND (
                    (state='pending' AND (typeof(next_attempt_at) NOT IN ('integer','real') OR next_attempt_at<=?))
                    OR (state='processing' AND (typeof(lease_until) NOT IN ('integer','real') OR lease_until<=?))
                ) ORDER BY occurred_at,event_id LIMIT 1""",
                (tenant_id, now, now),
            ).fetchone()
            if row is None:
                return None
            event = self._decode_cleanup_row(row)
            if row["attempts"] == 2**31 - 1:
                raise CleanupOutboxCorruption("Cleanup attempt counter exhausted")
            token = uuid4()
            try:
                claim = DocumentCleanupClaim(
                    event=event,
                    lease_token=token,
                    attempt=row["attempts"] + 1,
                    lease_expires_at=datetime.fromtimestamp(now + lease_seconds, timezone.utc),
                )
            except (ValidationError, ValueError, OverflowError):
                raise CleanupOutboxCorruption("Invalid cleanup claim clock or metadata") from None
            db.execute(
                """UPDATE document_cleanup_outbox
                SET state='processing',attempts=?,lease_token=?,lease_until=?
                WHERE tenant_id=? AND event_id=?""",
                (
                    claim.attempt,
                    str(token),
                    claim.lease_expires_at.timestamp(),
                    tenant_id,
                    str(event.event_id),
                ),
            )
        logger.info(
            {
                "event": "document_cleanup_claimed",
                "tenant_id": tenant_id,
                "event_id": str(claim.event.event_id),
                "attempt": claim.attempt,
            }
        )
        return claim

    def _owned_cleanup_row(self, db, *, tenant_id, event_id, lease_token, now):
        row = db.execute(
            "SELECT * FROM document_cleanup_outbox WHERE tenant_id=? AND event_id=?",
            (tenant_id, event_id),
        ).fetchone()
        if row is None:
            raise CleanupLeaseLost("Cleanup claim unavailable")
        self._decode_cleanup_row(row)
        if row["lease_token"] != lease_token:
            raise CleanupLeaseLost("Cleanup claim unavailable")
        if row["state"] == "completed":
            return row
        if row["state"] != "processing" or row["lease_until"] <= now:
            raise CleanupLeaseLost("Cleanup claim unavailable")
        return row

    def complete(self, *, tenant_id, event_id, lease_token):
        tenant_id = self._cleanup_tenant(tenant_id)
        event_id, token = self._cleanup_uuid(event_id), self._cleanup_uuid(lease_token)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time()
            row = self._owned_cleanup_row(
                db, tenant_id=tenant_id, event_id=event_id, lease_token=token, now=now
            )
            if row["state"] == "completed":
                return
            db.execute(
                """UPDATE document_cleanup_outbox SET state='completed',
                completed_at=?,lease_until=NULL,last_error_code=NULL
                WHERE tenant_id=? AND event_id=?""",
                (now, tenant_id, event_id),
            )

        logger.info(
            {"event": "document_cleanup_completed", "tenant_id": tenant_id, "event_id": event_id}
        )

    def retry(self, *, tenant_id, event_id, lease_token, delay_seconds, error_code):
        tenant_id = self._cleanup_tenant(tenant_id)
        event_id, token = self._cleanup_uuid(event_id), self._cleanup_uuid(lease_token)
        delay = self._cleanup_duration(delay_seconds, maximum=86400, allow_zero=True)
        if not isinstance(error_code, CleanupFailureCode):
            raise ValueError("Unknown cleanup failure code")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time()
            row = self._owned_cleanup_row(
                db, tenant_id=tenant_id, event_id=event_id, lease_token=token, now=now
            )
            if row["state"] == "completed":
                raise CleanupLeaseLost("Cleanup claim unavailable")
            db.execute(
                """UPDATE document_cleanup_outbox SET state='pending',
                lease_token=NULL,lease_until=NULL,next_attempt_at=?,last_error_code=?
                WHERE tenant_id=? AND event_id=?""",
                (now + delay, error_code.value, tenant_id, event_id),
            )
        logger.info(
            {
                "event": "document_cleanup_retry_scheduled",
                "tenant_id": tenant_id,
                "event_id": event_id,
                "delay_seconds": delay,
                "error_code": error_code.value,
            }
        )
