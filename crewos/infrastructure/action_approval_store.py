"""SQLite action approvals; claim is committed before invoking a mutating handler."""

import json
import sqlite3
from contextlib import contextmanager
from time import time
from uuid import uuid4

from crewos.application.services.action_approval import ApprovalConflict, ApprovalUnavailable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_approval (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, tool TEXT NOT NULL, version TEXT NOT NULL,
    payload TEXT NOT NULL, run_id TEXT, state TEXT NOT NULL,
    created REAL NOT NULL, expires REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS action_approval_tenant_run
ON action_approval(tenant, run_id, created);
CREATE TABLE IF NOT EXISTS demo_incident_note (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, incident_id TEXT NOT NULL, note TEXT NOT NULL
);
"""


class SQLiteActionApprovalStore:
    def __init__(self, path, *, ttl_seconds=900, clock=time):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("Approval lifetime must be between one second and one day")
        self.path, self.ttl, self.clock = path, ttl_seconds, clock
        with self._connection() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def propose(self, *, tenant, tool, version, payload, run_id):
        approval_id = str(uuid4())
        now = self.clock()
        with self._connection() as db:
            db.execute(
                "INSERT INTO action_approval VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (approval_id, tenant, tool, version, payload, run_id, now, now + self.ttl, now),
            )
        return approval_id

    def get(self, approval_id, tenant):
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM action_approval WHERE id = ? AND tenant = ?", (approval_id, tenant)
            ).fetchone()
        if row is None:
            raise ApprovalUnavailable("Approval unavailable")
        return dict(row)

    def list_run(self, tenant, run_id):
        with self._connection() as db:
            return [
                self.public(row)
                for row in db.execute(
                    "SELECT * FROM action_approval WHERE tenant = ? AND run_id = ? "
                    "ORDER BY created DESC LIMIT 100",
                    (tenant, run_id),
                )
            ]

    def review(self, approval_id, tenant, *, approve: bool):
        self._transition(
            approval_id, tenant, "pending", "approved" if approve else "rejected", True
        )
        return self.get(approval_id, tenant)

    def claim(self, approval_id, tenant):
        self._transition(approval_id, tenant, "approved", "executing", True)

    def finish(self, approval_id, tenant, *, state):
        if state not in {"completed", "uncertain"}:
            raise ValueError("Invalid execution outcome")
        self._transition(approval_id, tenant, "executing", state, False)

    def _transition(self, approval_id, tenant, before, after, expires):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            cursor = db.execute(
                "UPDATE action_approval SET state = ?, updated = ? "
                "WHERE id = ? AND tenant = ? AND state = ? AND (? = 0 OR expires > ?)",
                (after, now, approval_id, tenant, before, expires, now),
            )
            if not cursor.rowcount:
                present = db.execute(
                    "SELECT 1 FROM action_approval WHERE id = ? AND tenant = ?",
                    (approval_id, tenant),
                ).fetchone()
                if not present:
                    raise ApprovalUnavailable("Approval unavailable")
                raise ApprovalConflict("Approval cannot transition")

    def public(self, row):
        return {
            "approval_id": row["id"],
            "tool": row["tool"],
            "tool_version": row["version"],
            "arguments": json.loads(row["payload"]),
            "run_id": row["run_id"],
            "state": (
                "expired"
                if row["state"] in {"pending", "approved"} and row["expires"] <= self.clock()
                else row["state"]
            ),
            "expires_at": row["expires"],
        }

    def add_demo_note(self, *, tenant, incident_id, note):
        note_id = str(uuid4())
        with self._connection() as db:
            db.execute(
                "INSERT INTO demo_incident_note VALUES (?, ?, ?, ?)",
                (note_id, tenant, incident_id, note),
            )
        return note_id
