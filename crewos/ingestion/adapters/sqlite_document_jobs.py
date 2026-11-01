"""Durable job/idempotency operations sharing the document store transaction."""

import base64
from hashlib import sha256
import json
import re
import time
from uuid import uuid4

from crewos.chunking import ChunkingOptions
from crewos.ingestion.contracts import (
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    StoredDocumentVersion,
)
from crewos.ingestion.document_jobs import (
    DocumentJob,
    DocumentJobClaim,
    DocumentJobConflict,
    DocumentJobNotFound,
    DocumentSubmission,
)
from crewos.ingestion.document_store import DocumentStorageCorruption


class SQLiteDocumentJobs:
    def _initialize_jobs(self, db):
        db.execute(
            """CREATE TABLE IF NOT EXISTS document_jobs (
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, document_id TEXT NOT NULL,
            operation TEXT NOT NULL, key_hash TEXT NOT NULL, fingerprint TEXT NOT NULL,
            payload TEXT, options TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
            version INTEGER, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            token TEXT, lease_until REAL, published INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(tenant,key_hash))"""
        )

    def _job_row(self, db, job_id, tenant_id):
        row = db.execute(
            "SELECT * FROM document_jobs WHERE id=? AND tenant=?", (str(job_id), tenant_id)
        ).fetchone()
        if row is None:
            raise DocumentJobNotFound("Document job unavailable")
        return row

    def _job(self, db, row):
        available = (
            row["version"] is not None
            and db.execute(
                "SELECT 1 FROM document_versions WHERE tenant_id=? AND document_id=? AND version=?",
                (row["tenant"], row["document_id"], row["version"]),
            ).fetchone()
            is not None
        )
        return DocumentJob(
            job_id=row["id"],
            tenant_id=row["tenant"],
            document_id=row["document_id"],
            operation=row["operation"],
            state=row["state"],
            version=row["version"],
            attempts=row["attempts"],
            error_code=row["error"],
            publication_confirmed=bool(row["published"]),
            lease_active=row["state"] == "running"
            and row["lease_until"] is not None
            and row["lease_until"] > time.time(),
            result_available=available,
        )

    def submit_job(self, *, tenant_id, request, operation, options, idempotency_key):
        if (
            not isinstance(idempotency_key, str)
            or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key) is None
        ):
            raise ValueError("Invalid idempotency key")
        model = DocumentIngestionRequest if operation == "create" else DocumentUpdateRequest
        if operation not in {"create", "update"}:
            raise ValueError("Invalid document job operation")
        request = model.model_validate(request.model_dump(warnings=False))
        data = request.model_dump(mode="python", exclude={"content"})
        if operation == "update":
            data["document_id"] = str(request.document_id)
        data["content_b64"] = base64.b64encode(request.content).decode("ascii")
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        fingerprint = sha256((operation + ":" + payload).encode()).hexdigest()
        key_hash = sha256(idempotency_key.encode()).hexdigest()
        document_id = uuid4() if operation == "create" else request.document_id
        self._identity(tenant_id, document_id)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM document_jobs WHERE tenant=? AND key_hash=?", (tenant_id, key_hash)
            ).fetchone()
            if row is not None:
                if row["fingerprint"] != fingerprint:
                    raise DocumentJobConflict("Idempotency key reused with different input")
                return DocumentSubmission(job=self._job(db, row), replayed=True)
            job_id = str(uuid4())
            now = time.time()
            db.execute(
                """INSERT INTO document_jobs
                (id,tenant,document_id,operation,key_hash,fingerprint,payload,options,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    tenant_id,
                    str(document_id),
                    operation,
                    key_hash,
                    fingerprint,
                    payload,
                    options.model_dump_json(),
                    now,
                    now,
                ),
            )
            return DocumentSubmission(
                job=self._job(db, self._job_row(db, job_id, tenant_id)), replayed=False
            )

    def get_job(self, job_id, tenant_id):
        with self._connection() as db:
            return self._job(db, self._job_row(db, job_id, tenant_id))

    def claim_job(self, job_id, tenant_id):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job_row(db, job_id, tenant_id)
            if row["state"] in {"succeeded", "failed"}:
                return None
            if row["state"] == "running" and row["lease_until"] > time.time():
                raise DocumentJobConflict("Document job already has an active attempt")
            if row["attempts"] >= self.job_max_attempts:
                db.execute(
                    "UPDATE document_jobs SET state='failed',error='AttemptLimit',payload=NULL,token=NULL,lease_until=NULL WHERE id=?",
                    (str(job_id),),
                )
                return None
            try:
                data = json.loads(row["payload"])
                content = base64.b64decode(data.pop("content_b64"), validate=True)
                model = (
                    DocumentIngestionRequest
                    if row["operation"] == "create"
                    else DocumentUpdateRequest
                )
                request = model.model_validate({**data, "content": content})
                options = ChunkingOptions.model_validate_json(row["options"])
            except (ValueError, TypeError, KeyError):
                raise DocumentStorageCorruption("Stored document job is invalid") from None
            # Validate target identity before using any persisted source payload.
            if row["operation"] == "update" and str(request.document_id) != row["document_id"]:
                raise DocumentStorageCorruption("Document job identity is inconsistent")
            token = str(uuid4())
            db.execute(
                "UPDATE document_jobs SET state='running',attempts=attempts+1,token=?,lease_until=?,updated=? WHERE id=?",
                (token, time.time() + self.job_lease_seconds, time.time(), str(job_id)),
            )
            return DocumentJobClaim(
                job=self._job(db, self._job_row(db, job_id, tenant_id)),
                request=request,
                options=options,
                token=token,
            )

    def commit_job(self, claim, record):
        record = StoredDocumentVersion.model_validate_json(record.model_dump_json())
        if record.tenant_id != claim.job.tenant_id:
            raise DocumentJobConflict("Document job tenant differs from result")
        if claim.job.operation == "create":
            record = StoredDocumentVersion.model_validate(
                {**record.model_dump(), "document_id": claim.job.document_id}
            )
        elif record.document_id != claim.job.document_id:
            raise DocumentJobConflict("Document job target differs from result")
        expected = 1 if claim.job.operation == "create" else claim.request.expected_version + 1
        if record.version != expected:
            raise DocumentJobConflict("Document job revision differs from result")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job_row(db, claim.job.job_id, claim.job.tenant_id)
            if (
                row["state"] != "running"
                or row["token"] != claim.token
                or row["lease_until"] <= time.time()
            ):
                raise DocumentJobConflict("Document job attempt lost ownership")
            self._append(db, record)
            db.execute(
                "UPDATE document_jobs SET state='succeeded',version=?,error=NULL,payload=NULL,token=NULL,lease_until=NULL,updated=? WHERE id=?",
                (record.version, time.time(), str(claim.job.job_id)),
            )
        return record

    def fail_job(self, claim, error_code):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_code) is None:
            raise ValueError("Invalid failure code")
        with self._connection() as db:
            db.execute(
                "UPDATE document_jobs SET state='failed',error=?,payload=CASE WHEN attempts>=? THEN NULL ELSE payload END,token=NULL,lease_until=NULL,updated=? WHERE id=? AND tenant=? AND token=? AND state='running'",
                (
                    error_code,
                    self.job_max_attempts,
                    time.time(),
                    str(claim.job.job_id),
                    claim.job.tenant_id,
                    claim.token,
                ),
            )

    def retry_job(self, job_id, tenant_id):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job_row(db, job_id, tenant_id)
            if row["state"] == "queued":
                return self._job(db, row)
            if (
                row["state"] == "succeeded"
                or (row["state"] == "running" and row["lease_until"] > time.time())
                or row["attempts"] >= self.job_max_attempts
                or row["payload"] is None
            ):
                raise DocumentJobConflict("Document job cannot be retried")
            db.execute(
                "UPDATE document_jobs SET state='queued',error=NULL,token=NULL,lease_until=NULL,published=0,updated=? WHERE id=?",
                (time.time(), str(job_id)),
            )
            return self._job(db, self._job_row(db, job_id, tenant_id))

    def mark_job_published(self, job_id, tenant_id):
        with self._connection() as db:
            self._job_row(db, job_id, tenant_id)
            db.execute(
                "UPDATE document_jobs SET published=1,updated=? WHERE id=?",
                (time.time(), str(job_id)),
            )
