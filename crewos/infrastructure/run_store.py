"""Single-host durable SQLite run records. No Redis result TTL dependency."""

from contextlib import contextmanager
from functools import lru_cache
import json
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from math import isfinite
import time
from uuid import uuid4

from crewos.application.interfaces.run_store import (
    RunCancelled,
    RunConflict,
    RunLeaseLost,
    RunNotFound,
    UnsafeReplay,
)

TERMINAL = {"succeeded", "cancelled", "exhausted", "needs_review"}


class SQLiteRunStore:
    def __init__(self, path: str, *, lease_seconds: float = 45, max_attempts: int = 3):
        self.path = str(path)
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        if (
            path == ":memory:"
            or not isfinite(lease_seconds)
            or lease_seconds <= 0
            or type(max_attempts) is not int
            or max_attempts < 1
        ):
            raise ValueError(
                "A file-backed database and positive lease/attempt limits are required"
            )
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported run database schema version")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, job TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
                deadline REAL, duration REAL NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}',
                result TEXT, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, idem_key TEXT, published INTEGER NOT NULL DEFAULT 0,
                UNIQUE(tenant,idem_key)
            )"""
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS agent_attempts (token TEXT PRIMARY KEY, run_id TEXT NOT NULL, number INTEGER NOT NULL, state TEXT NOT NULL, started REAL NOT NULL, ended REAL, error TEXT)"
            )
            db.execute("PRAGMA user_version=1")
        os.chmod(path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _tx(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def _row(self, db, run_id, tenant_id):
        row = db.execute(
            "SELECT * FROM agent_runs WHERE id=? AND tenant=?", (run_id, tenant_id)
        ).fetchone()
        if row is None:
            raise RunNotFound("Agent run unavailable")
        return row

    @staticmethod
    def _decode(row):
        data = dict(row)
        for key in ("job", "checkpoint", "result"):
            data[key] = json.loads(data[key]) if data[key] is not None else None
        data["cancel_requested"] = bool(data["cancel_requested"])
        return data

    def create(
        self, job: dict, *, duration_seconds: float = 120, idempotency_key: str | None = None
    ):
        if not isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("Run duration must be finite and positive")
        now = time.time()
        with self._tx() as db:
            key = sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
            if key:
                existing = db.execute(
                    "SELECT * FROM agent_runs WHERE tenant=? AND idem_key=?",
                    (job["tenant_id"], key),
                ).fetchone()
                if existing:
                    old = json.loads(existing["job"])
                    if {k: v for k, v in old.items() if k != "agent_run_id"} != {
                        k: v for k, v in job.items() if k != "agent_run_id"
                    }:
                        raise RunConflict("Idempotency key reused with different input")
                    return self._decode(existing)
            db.execute(
                "INSERT INTO agent_runs(id,tenant,job,state,created,updated,duration,idem_key) VALUES(?,?,?,?,?,?,?,?)",
                (
                    job["agent_run_id"],
                    job["tenant_id"],
                    json.dumps(job, sort_keys=True),
                    "queued",
                    now,
                    now,
                    duration_seconds,
                    key,
                ),
            )
            return self._decode(self._row(db, job["agent_run_id"], job["tenant_id"]))

    def get(self, run_id, tenant_id):
        with self._tx() as db:
            row = self._row(db, run_id, tenant_id)
            if (
                row["state"] == "running"
                and row["cancel_requested"]
                and row["lease_until"] <= time.time()
            ):
                db.execute(
                    "UPDATE agent_runs SET state='cancelled',lease_token=NULL,lease_until=NULL,updated=? WHERE id=?",
                    (time.time(), run_id),
                )
                db.execute(
                    "UPDATE agent_attempts SET state='cancelled',ended=? WHERE token=?",
                    (time.time(), row["lease_token"]),
                )
            if (
                row["state"] == "running"
                and not row["cancel_requested"]
                and row["lease_until"] <= time.time()
            ):
                cp = json.loads(row["checkpoint"])
                if cp.get("pending") and not cp.get("replay_safe"):
                    db.execute(
                        "UPDATE agent_runs SET state='needs_review',error='UnsafeReplay',lease_token=NULL,lease_until=NULL,updated=? WHERE id=?",
                        (time.time(), run_id),
                    )
                    db.execute(
                        "UPDATE agent_attempts SET state='needs_review',ended=?,error='UnsafeReplay' WHERE token=?",
                        (time.time(), row["lease_token"]),
                    )
            current = self._row(db, run_id, tenant_id)
            if (
                current["state"] == "running"
                and current["lease_until"] <= time.time()
                and current["deadline"] <= time.time()
            ):
                db.execute(
                    "UPDATE agent_runs SET state='exhausted',error='AgentDeadlineExceeded',lease_token=NULL,lease_until=NULL,updated=? WHERE id=?",
                    (time.time(), run_id),
                )
                db.execute(
                    "UPDATE agent_attempts SET state='exhausted',ended=?,error='AgentDeadlineExceeded' WHERE token=?",
                    (time.time(), current["lease_token"]),
                )
            data = self._decode(self._row(db, run_id, tenant_id))
            data["attempt_history"] = [
                dict(row)
                for row in db.execute(
                    "SELECT number,state,started,ended,error FROM agent_attempts WHERE run_id=? ORDER BY number",
                    (run_id,),
                )
            ]
            return data

    def mark_published(self, run_id, tenant_id):
        with self._tx() as db:
            self._row(db, run_id, tenant_id)
            db.execute(
                "UPDATE agent_runs SET published=1,updated=? WHERE id=?", (time.time(), run_id)
            )

    def claim(self, job):
        now = time.time()
        with self._tx() as db:
            row = self._row(db, job["agent_run_id"], job["tenant_id"])
            if row["job"] != json.dumps(job, sort_keys=True):
                raise RunConflict("Job differs from registered submission")
            if row["state"] == "succeeded":
                return self._decode(row)
            if row["cancel_requested"] or row["state"] == "cancelled":
                raise RunCancelled("Run cancelled")
            if row["state"] in TERMINAL:
                raise RunConflict("Run cannot execute in its current state")
            if row["state"] == "failed":
                raise RunConflict("Explicit resume is required after a failed run")
            if row["state"] == "running" and row["lease_until"] > now:
                raise RunConflict("Run already has an active attempt")
            checkpoint = json.loads(row["checkpoint"])
            if checkpoint.get("pending") and not checkpoint.get("replay_safe"):
                raise UnsafeReplay("Pending tool outcome requires manual review")
            if row["attempts"] >= self.max_attempts:
                raise RunConflict("Attempt limit reached")
            if row["lease_token"]:
                db.execute(
                    "UPDATE agent_attempts SET state='expired',ended=? WHERE token=?",
                    (now, row["lease_token"]),
                )
            token = str(uuid4())
            db.execute(
                "INSERT INTO agent_attempts(token,run_id,number,state,started) VALUES(?,?,?,?,?)",
                (token, row["id"], row["attempts"] + 1, "running", now),
            )
            db.execute(
                "UPDATE agent_runs SET state='running',attempts=attempts+1,lease_token=?,lease_until=?,deadline=COALESCE(deadline,?),updated=? WHERE id=?",
                (
                    token,
                    now + self.lease_seconds,
                    now + row["duration"],
                    now,
                    row["id"],
                ),
            )
            return self._decode(self._row(db, row["id"], row["tenant"]))

    def _owned_attempt(self, db, run_id, tenant_id, token):
        row = self._row(db, run_id, tenant_id)
        if (
            row["state"] != "running"
            or row["lease_token"] != token
            or row["lease_until"] <= time.time()
        ):
            raise RunLeaseLost("Attempt no longer owns the run lease")
        return row

    def guard(self, run_id, tenant_id, token):
        with self._tx() as db:
            row = self._owned_attempt(db, run_id, tenant_id, token)
            if row["cancel_requested"]:
                raise RunCancelled("Run cancelled")
            now = time.time()
            db.execute(
                "UPDATE agent_runs SET lease_until=? WHERE id=?", (now + self.lease_seconds, run_id)
            )
            return row["deadline"] - now

    def checkpoint(self, run_id, tenant_id, token, checkpoint):
        with self._tx() as db:
            row = self._owned_attempt(db, run_id, tenant_id, token)
            if row["cancel_requested"]:
                raise RunCancelled("Run cancelled")
            db.execute(
                "UPDATE agent_runs SET checkpoint=?,updated=?,lease_until=? WHERE id=?",
                (
                    json.dumps(checkpoint),
                    time.time(),
                    time.time() + self.lease_seconds,
                    run_id,
                ),
            )

    def finish(self, run_id, tenant_id, token, *, state, result=None, error=None):
        if state not in {"succeeded", "failed", "cancelled", "exhausted", "needs_review"}:
            raise ValueError("Invalid terminal state")
        with self._tx() as db:
            row = self._owned_attempt(db, run_id, tenant_id, token)
            if row["cancel_requested"]:
                state, result, error = "cancelled", None, "cancelled"
            db.execute(
                "UPDATE agent_attempts SET state=?,ended=?,error=? WHERE token=?",
                (state, time.time(), error, token),
            )
            db.execute(
                "UPDATE agent_runs SET state=?,result=?,error=?,lease_token=NULL,lease_until=NULL,updated=? WHERE id=?",
                (
                    state,
                    json.dumps(result) if result is not None else None,
                    error,
                    time.time(),
                    run_id,
                ),
            )

    def cancel(self, run_id, tenant_id):
        with self._tx() as db:
            row = self._row(db, run_id, tenant_id)
            if row["state"] not in TERMINAL:
                state = (
                    "running"
                    if row["state"] == "running" and row["lease_until"] > time.time()
                    else "cancelled"
                )
                db.execute(
                    "UPDATE agent_runs SET cancel_requested=1,state=?,updated=? WHERE id=?",
                    (state, time.time(), run_id),
                )
            return self._decode(self._row(db, run_id, tenant_id))

    def resume(self, run_id, tenant_id):
        with self._tx() as db:
            row = self._row(db, run_id, tenant_id)
            if row["cancel_requested"] or row["state"] in TERMINAL:
                raise RunConflict("Run is not resumable")
            if row["state"] == "running" and row["lease_until"] > time.time():
                raise RunConflict("Run still has an active attempt")
            if row["attempts"] >= self.max_attempts:
                raise RunConflict("Attempt limit reached")
            cp = json.loads(row["checkpoint"])
            if cp.get("pending") and not cp.get("replay_safe"):
                raise UnsafeReplay("Pending write cannot be replayed")
            if row["deadline"] is not None and row["deadline"] <= time.time():
                raise RunConflict("Original run deadline has expired")
            db.execute(
                "UPDATE agent_runs SET state='queued',lease_token=NULL,lease_until=NULL,published=0,updated=? WHERE id=?",
                (time.time(), run_id),
            )
            return self._decode(self._row(db, run_id, tenant_id))


@lru_cache
def get_run_store():
    from crewos.core.config import settings

    return SQLiteRunStore(
        settings.AGENT_RUN_DB_PATH,
        lease_seconds=settings.AGENT_LEASE_SECONDS,
        max_attempts=settings.AGENT_MAX_ATTEMPTS,
    )
